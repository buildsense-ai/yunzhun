// Package mailx: mail-side helpers — storage-ref extraction, checksums,
// MIME parse, IMAP. Ported from the Python app/mail/* modules.
package mailx

import (
	"net/url"
	"regexp"
	"strings"
)

// URL candidates incl. native object-store schemes used by delivery mails
// (e.g. Novogene "数据交付位置：oss://bucket/key").
var urlRe = regexp.MustCompile(`(?:https?://|(?:oss|cos|obs|s3)://)[^\s<>"'` + "`" + `)\]}]+`)

var trailPunct = ".,;:!?'\""

var presignParams = []string{"x-amz-signature", "x-amz-expires", "signature", "ossaccesskeyid", "q-sign-algorithm", "q-sign-time", "sig", "se=", "sp="}

type StorageRef struct {
	Provider  string
	Bucket    string
	Key       string
	Region    string
	URL       string
	Presigned bool
}

func isPresigned(query string) bool {
	q := strings.ToLower(query)
	for _, p := range presignParams {
		if strings.Contains(q, p) {
			return true
		}
	}
	return false
}

var (
	reOSSVhost = regexp.MustCompile(`^([^>.]+)\.oss(?:-dfile)?-([a-z0-9-]+?)(?:-internal)?\.aliyuncs\.com$`)
	reOSSPath  = regexp.MustCompile(`^oss(?:-dfile)?-([a-z0-9-]+?)(?:-internal)?\.aliyuncs\.com$`)
	reCOS      = regexp.MustCompile(`^([^.]+)\.cos\.([^.]+)\.myqcloud\.com$`)
	reOBSVhost = regexp.MustCompile(`^([^.]+)\.obs\.([^.]+)\.myhuaweicloud\.com$`)
	reOBSPath  = regexp.MustCompile(`^obs\.([^.]+)\.myhuaweicloud\.com$`)
	reS3Vhost  = regexp.MustCompile(`^([^.]+)\.s3(?:[-.]([\w-]+))?\.amazonaws\.com(?:\.cn)?$`)
	reS3Path   = regexp.MustCompile(`^s3[.-]([\w-]+)\.amazonaws\.com(?:\.cn)?$`)
	reAzure    = regexp.MustCompile(`^([^.]+)\.blob\.core\.windows\.net$`)
)

func firstSeg(path string) string {
	p := strings.TrimPrefix(path, "/")
	if i := strings.IndexByte(p, '/'); i >= 0 {
		return p[:i]
	}
	return p
}

func restAsKey(path string) string {
	p := strings.TrimPrefix(path, "/")
	if i := strings.IndexByte(p, '/'); i >= 0 {
		return p[i+1:]
	}
	return ""
}

func pathKey(path string) string { return strings.TrimPrefix(path, "/") }

// Classify a single URL into a StorageRef, or nil.
func Classify(rawurl string) *StorageRef {
	u, err := url.Parse(rawurl)
	if err != nil || u.Host == "" {
		return nil
	}
	scheme, host, path := strings.ToLower(u.Scheme), strings.ToLower(u.Hostname()), u.Path
	pre := isPresigned(u.RawQuery)

	switch scheme {
	case "oss", "cos", "obs", "s3":
		prov := map[string]string{"oss": "aliyun-oss", "cos": "tencent-cos", "obs": "huawei-obs", "s3": "s3-compatible"}[scheme]
		return &StorageRef{prov, host, pathKey(path), "", rawurl, pre}
	}
	if scheme != "http" && scheme != "https" {
		return nil
	}

	if m := reOSSVhost.FindStringSubmatch(host); m != nil {
		return &StorageRef{"aliyun-oss", m[1], pathKey(path), m[2], rawurl, pre}
	}
	if m := reOSSPath.FindStringSubmatch(host); m != nil {
		return &StorageRef{"aliyun-oss", firstSeg(path), restAsKey(path), m[1], rawurl, pre}
	}
	if m := reCOS.FindStringSubmatch(host); m != nil {
		return &StorageRef{"tencent-cos", m[1], pathKey(path), m[2], rawurl, pre}
	}
	if m := reOBSVhost.FindStringSubmatch(host); m != nil {
		return &StorageRef{"huawei-obs", m[1], pathKey(path), m[2], rawurl, pre}
	}
	if m := reOBSPath.FindStringSubmatch(host); m != nil {
		return &StorageRef{"huawei-obs", firstSeg(path), restAsKey(path), m[1], rawurl, pre}
	}
	if m := reS3Vhost.FindStringSubmatch(host); m != nil {
		return &StorageRef{"aws-s3", m[1], pathKey(path), m[2], rawurl, pre}
	}
	if m := reS3Path.FindStringSubmatch(host); m != nil {
		return &StorageRef{"aws-s3", firstSeg(path), restAsKey(path), m[1], rawurl, pre}
	}
	if host == "storage.googleapis.com" {
		return &StorageRef{"gcs", firstSeg(path), restAsKey(path), "", rawurl, pre}
	}
	if strings.HasSuffix(host, ".storage.googleapis.com") {
		return &StorageRef{"gcs", strings.TrimSuffix(host, ".storage.googleapis.com"), pathKey(path), "", rawurl, pre}
	}
	if m := reAzure.FindStringSubmatch(host); m != nil {
		return &StorageRef{"azure-blob", m[1] + "/" + firstSeg(path), restAsKey(path), "", rawurl, pre}
	}
	if strings.HasPrefix(host, "pan.") {
		return &StorageRef{"cloud-drive", host, pathKey(path), "", rawurl, pre}
	}
	if pre && (strings.Contains(u.RawQuery, "X-Amz-Signature") || strings.Contains(u.RawQuery, "Signature") || strings.Contains(u.RawQuery, "OSSAccessKeyId")) {
		return &StorageRef{"s3-compatible", firstSeg(path), restAsKey(path), "", rawurl, true}
	}
	return nil
}

// ExtractStorageRefs scans text/html sources, deduped, order-stable.
func ExtractStorageRefs(sources ...string) []StorageRef {
	seen := map[[4]string]bool{}
	var out []StorageRef
	for _, src := range sources {
		if src == "" {
			continue
		}
		for _, raw := range urlRe.FindAllString(src, -1) {
			u := strings.TrimRight(raw, trailPunct)
			ref := Classify(u)
			if ref == nil {
				continue
			}
			k := [4]string{ref.Provider, ref.Bucket, ref.Key, ref.URL}
			if seen[k] {
				continue
			}
			seen[k] = true
			out = append(out, *ref)
		}
	}
	return out
}
