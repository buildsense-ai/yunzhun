package mailx

import (
	"regexp"
	"strings"
)

// "<md5> <file>" pairs embedded in delivery mail text.
var (
	md5Hex        = `[0-9a-fA-F]{32}`
	fileExt       = `[\w.\-]+\.(?:fastq|fq|gz|tar|zip|bz2|csv|tsv|txt|pdf|h5|loom|bam|bai)`
	reMD5ThenName = regexp.MustCompile(`(?i)\b(` + md5Hex + `)\b[\s:=*]+(` + fileExt + `)`)
	reNameThenMD5 = regexp.MustCompile(`(?i)(` + fileExt + `)[\s:=*]+(?:md5[\s:=]*)?(` + md5Hex + `)`)
	reMD5Func     = regexp.MustCompile(`(?i)md5\s*\(\s*(` + fileExt + `)\s*\)\s*[=:]\s*(` + md5Hex + `)`)
)

// ExtractChecksums returns {filename: md5hex_lower}.
func ExtractChecksums(sources ...string) map[string]string {
	out := map[string]string{}
	for _, src := range sources {
		for _, m := range reMD5ThenName.FindAllStringSubmatch(src, -1) {
			if _, ok := out[m[2]]; !ok {
				out[m[2]] = strings.ToLower(m[1])
			}
		}
		for _, m := range reNameThenMD5.FindAllStringSubmatch(src, -1) {
			if _, ok := out[m[1]]; !ok {
				out[m[1]] = strings.ToLower(m[2])
			}
		}
		for _, m := range reMD5Func.FindAllStringSubmatch(src, -1) {
			if _, ok := out[m[1]]; !ok {
				out[m[1]] = strings.ToLower(m[2])
			}
		}
	}
	return out
}
