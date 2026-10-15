package mailx

import "testing"

func TestAssayDir(t *testing.T) {
	cases := []struct{ subj, body, want string }{
		{"诺禾致源10x转录组测序数据交付", "", "10X"},
		{"华大转录组交付通知", "", "huada"},
		{"免疫组 VDJ 数据", "", "vdj"},
		{"ATAC-seq 数据交付", "", "ATAC"},
		{"demultiplex 拆分结果", "", "demultiplex"},
		{"10X空转项目数据", "", "10X空转"},
		{"华大空转交付", "", "华大空转"},
		{"其他通知", "", "other"},
	}
	for _, c := range cases {
		if got := AssayDir(c.subj, c.body); got != c.want {
			t.Errorf("AssayDir(%q) = %q, want %q", c.subj, got, c.want)
		}
	}
}
