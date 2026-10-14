// Package pull: object-storage downloads — presigned HTTPS GET + Aliyun OSS
// SignV1 (HMAC-SHA1, stdlib only). COS/OBS/S3 credentialed pulls are TODO;
// presigned URLs from those providers already work via the HTTPS path.
package pull

import (
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"goapp/internal/store"
)

var httpClient = &http.Client{Timeout: 10 * time.Minute}

// ---------- presigned / public URL path ----------

// FetchURL downloads an https object URL (presigned or public) — no creds needed.
func FetchURL(url string, maxBytes int64) ([]byte, error) {
	resp, err := httpClient.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("GET %s: HTTP %d", url, resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, maxBytes+1))
}

// ---------- unified puller ----------

type Puller interface {
	Read(key string) ([]byte, error)
	List(prefix string) (files, dirs []string, err error)
	Stat(key string) (int64, error)
}

func pullerFor(st *store.Store) (Puller, error) {
	switch st.Provider {
	case "aliyun-oss":
		return newOSS(st)
	case "tencent-cos":
		return newCOS(st)
	case "huawei-obs":
		return newOBS(st)
	case "aws-s3", "s3-compatible":
		return newS3(st)
	default:
		return nil, fmt.Errorf("credentialed pull for %s not wired yet (presigned https links work)", st.Provider)
	}
}

// ---------- pipeline-facing ----------

func md5hex(b []byte) string {
	h := md5.Sum(b)
	return hex.EncodeToString(h[:])
}

// PullRef downloads one ref's objects through sink under base (e.g. "msg-7").
// Returns pulled display paths and per-key failures.
// Verifies md5 (when the mail carried one) or remote size before writing.
func PullRef(st *store.Store, ref *store.ObjectRef, sink Sink, base string, recursive bool, maxFiles int, doneKeys map[string]bool) (pulled []string, failures []string, truncated bool) {
	write := func(key string, data []byte, remoteSize int64) (string, string) {
		rel := base + "/" + key
		if errMsg := verifyData(data, ref.ExpectedMD5, remoteSize); errMsg != "" {
			return "", errMsg
		}
		if err := sink.Write(rel, data); err != nil {
			return "", err.Error()
		}
		return sink.Display(rel), ""
	}

	// presigned/public https ref: plain GET, no store needed
	if st == nil {
		if !ref.Presigned || !strings.HasPrefix(strings.ToLower(ref.URL), "http") {
			return nil, []string{"no store credentials and not a presigned https url"}, false
		}
		key := filepath.Base(strings.Split(ref.Key, "?")[0])
		if doneKeys[key] && sink.Exists(base+"/"+key) {
			return nil, nil, false
		}
		data, err := FetchURL(ref.URL, 20<<30)
		if err != nil {
			return nil, []string{err.Error()}, false
		}
		disp, fail := write(key, data, -1)
		if fail != "" {
			return nil, []string{key + ": " + fail}, false
		}
		return []string{disp}, nil, false
	}

	pl, err := pullerFor(st)
	if err != nil {
		return nil, []string{err.Error()}, false
	}

	var keys []string
	if recursive {
		files, err := walkPrefix(pl, ref.Key, maxFiles)
		if err == nil && len(files) > 0 {
			keys = files
		} else {
			keys = []string{ref.Key}
		}
		truncated = len(files) >= maxFiles
	} else {
		keys = []string{ref.Key}
	}

	for _, key := range keys {
		if unsafeKey(key) {
			failures = append(failures, "unsafe object key "+key)
			continue
		}
		if doneKeys[key] && sink.Exists(base+"/"+key) {
			continue
		}
		data, err := pl.Read(key)
		if err != nil {
			failures = append(failures, key+": "+err.Error())
			continue
		}
		var remoteSize int64 = -1
		if ref.ExpectedMD5 == "" {
			if n, err := pl.Stat(key); err == nil {
				remoteSize = n
			}
		}
		disp, fail := write(key, data, remoteSize)
		if fail != "" {
			failures = append(failures, key+": "+fail)
			continue
		}
		pulled = append(pulled, disp)
	}
	return pulled, failures, truncated
}

func walkPrefix(pl Puller, prefix string, maxFiles int) ([]string, error) {
	if !strings.HasSuffix(prefix, "/") {
		prefix += "/"
	}
	stack := []string{prefix}
	var files []string
	for len(stack) > 0 && len(files) < maxFiles {
		cur := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		f, d, err := pl.List(cur)
		if err != nil {
			return files, err
		}
		stack = append(stack, d...)
		for _, k := range f {
			if k != cur {
				files = append(files, k)
			}
		}
	}
	return files, nil
}

func verifyData(data []byte, expectedMD5 string, remoteSize int64) string {
	if expectedMD5 != "" {
		if got := md5hex(data); got != expectedMD5 {
			return fmt.Sprintf("md5 mismatch: got %s, expected %s", got, expectedMD5)
		}
	} else if remoteSize >= 0 && remoteSize != int64(len(data)) {
		return fmt.Sprintf("size mismatch: got %d, remote %d", len(data), remoteSize)
	}
	return ""
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
