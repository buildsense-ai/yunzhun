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
	"os"
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

var unsafePath = strings.NewReplacer("..", "", "\\", "")

func safeLocal(base, key string) (string, error) {
	if strings.Contains(key, "..") || strings.HasPrefix(key, "/") || strings.ContainsRune(key, 0) || strings.ContainsRune(key, '\\') {
		return "", fmt.Errorf("unsafe object key %q", key)
	}
	p := filepath.Join(base, filepath.FromSlash(key))
	abs, _ := filepath.Abs(p)
	absBase, _ := filepath.Abs(base)
	if !strings.HasPrefix(abs, absBase+string(os.PathSeparator)) {
		return "", fmt.Errorf("key escapes download dir: %q", key)
	}
	return abs, nil
}

func md5hex(b []byte) string {
	h := md5.Sum(b)
	return hex.EncodeToString(h[:])
}

// PullRef downloads one ref's objects into dest. Returns pulled local paths and per-key failures.
// Verifies md5 (when the mail carried one) or remote size.
func PullRef(st *store.Store, ref *store.ObjectRef, dest string, recursive bool, maxFiles int, doneKeys map[string]string) (pulled []string, failures []string, truncated bool) {
	// presigned/public https ref: plain GET, no store needed
	if st == nil {
		if !ref.Presigned || !strings.HasPrefix(strings.ToLower(ref.URL), "http") {
			return nil, []string{"no store credentials and not a presigned https url"}, false
		}
		target, err := safeLocal(dest, filepath.Base(strings.Split(ref.Key, "?")[0]))
		if err != nil {
			return nil, []string{err.Error()}, false
		}
		if _, ok := doneKeys[ref.Key]; ok && fileExists(doneKeys[ref.Key]) {
			return nil, nil, false
		}
		data, err := FetchURL(ref.URL, 20<<30)
		if err != nil {
			return nil, []string{err.Error()}, false
		}
		if fail := verifyAndWrite(target, data, ref.ExpectedMD5, -1); fail != "" {
			return nil, []string{ref.Key + ": " + fail}, false
		}
		return []string{target}, nil, false
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
		if old, ok := doneKeys[key]; ok && fileExists(old) {
			continue
		}
		target, err := safeLocal(dest, key)
		if err != nil {
			failures = append(failures, err.Error())
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
		if fail := verifyAndWrite(target, data, ref.ExpectedMD5, remoteSize); fail != "" {
			failures = append(failures, key+": "+fail)
			continue
		}
		pulled = append(pulled, target)
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

func verifyAndWrite(target string, data []byte, expectedMD5 string, remoteSize int64) string {
	if expectedMD5 != "" {
		if got := md5hex(data); got != expectedMD5 {
			return fmt.Sprintf("md5 mismatch: got %s, expected %s", got, expectedMD5)
		}
	} else if remoteSize >= 0 && remoteSize != int64(len(data)) {
		return fmt.Sprintf("size mismatch: got %d, remote %d", len(data), remoteSize)
	}
	os.MkdirAll(filepath.Dir(target), 0o755)
	tmp := target + ".part"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err.Error()
	}
	if err := os.Rename(tmp, target); err != nil {
		return err.Error()
	}
	return ""
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.Size() > 0
}

var _ = unsafePath // reserved

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
