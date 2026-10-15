// Assay directory naming: delivery mails land under <date>/<canonical-dir>/
// per the lab convention:
//
//   2026-9-28/
//     10X/           10x转录组
//     huada/         华大转录组
//     vdj/           免疫组
//     ATAC/          ATAC
//     demultiplex/   demultiplex 拆分
//     script/        (scripts — manual, not mail-driven)
//     10X空转/       10X spatial (canonical name = literal)
//     华大空转/       BGI spatial (canonical name = literal)
//     other/         unmatched
package mailx

import "strings"

// AssayDir maps subject/body keywords to the canonical directory name.
// Order matters: spatial variants before their base assays.
func AssayDir(subject, body string) string {
	s := strings.ToLower(subject + "\n" + body)
	has := func(k string) bool { return strings.Contains(s, strings.ToLower(k)) }

	switch {
	case has("10x空转") || has("10x空间转录") || (has("10x") && has("空间")):
		return "10X空转"
	case has("华大空转") || (has("华大") && has("空间")):
		return "华大空转"
	case has("demultiplex") || has("拆分"):
		return "demultiplex"
	case has("免疫组") || has("vdj"):
		return "vdj"
	case has("atac"):
		return "ATAC"
	case has("10x转录组") || has("10x转录") || (has("10x") && has("转录")):
		return "10X"
	case has("华大转录组") || has("华大"):
		return "huada"
	default:
		return "other"
	}
}
