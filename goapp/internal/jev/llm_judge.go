// LLM-backed judger: same judgment contract as Jev's SystemOne, implemented
// over any OpenAI-compatible chat endpoint (e.g. Vercel AI Gateway free tier).
// Used when the typesafe-ai/jev model is unavailable.
package jev

import (
	"encoding/json"
	"fmt"
	"strings"
)

const judgePrompt = `You are an email triage classifier for a bio-data intake pipeline.
Analyze the email and respond with JSON only:
{"category":"<one>","category_confidence":<0-1>,"storage_delivery":<0-1>,
"action_required":<0|1|2>,"assay":"<one>","assay_confidence":<0-1>}

category ∈ {delivery(数据/文件交付，含下载地址或交付位置), billing, security,
notification, personal, other}
storage_delivery = probability the mail contains storage/delivery info
action_required = 0 none, 1 read-later, 2 urgent
assay ∈ {10X(10x转录组/scRNA-seq/Chromium), huada(华大转录组/DNBSEQ),
vdj(免疫组/VDJ/TCR/BCR), ATAC(ATAC-seq), demultiplex(拆分),
10X空转(10x空间转录/Visium), 华大空转(华大空间转录/Stereo-seq), other}`

type llmJudgeResp struct {
	Category        string  `json:"category"`
	CategoryConf    float64 `json:"category_confidence"`
	StorageDelivery float64 `json:"storage_delivery"`
	ActionRequired  int     `json:"action_required"`
	Assay           string  `json:"assay"`
	AssayConf       float64 `json:"assay_confidence"`
}

// Judge asks the chat model for a structured judgment and maps it into
// the same Answers contract the Jev provider produces.
func (l *LLM) Judge(state string) (*Answers, error) {
	payload := map[string]any{
		"model": l.Model,
		"messages": []map[string]string{
			{"role": "system", "content": judgePrompt},
			{"role": "user", "content": truncate(state, 6000)},
		},
		"response_format": map[string]string{"type": "json_object"},
	}
	raw, err := l.post(payload)
	if err != nil {
		return nil, err
	}
	var out struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(raw, &out); err != nil || len(out.Choices) == 0 {
		return nil, fmt.Errorf("llm judge decode: %v", err)
	}
	content := out.Choices[0].Message.Content
	if i := strings.Index(content, "{"); i >= 0 {
		content = content[i:]
	}
	var r llmJudgeResp
	if err := json.Unmarshal([]byte(content), &r); err != nil {
		return nil, fmt.Errorf("llm judge json: %w", err)
	}
	if r.Category == "" {
		r.Category = "other"
	}
	if r.Assay == "" {
		r.Assay = "other"
	}
	if r.CategoryConf == 0 {
		r.CategoryConf = 0.8
	}
	if r.AssayConf == 0 {
		r.AssayConf = 0.8
	}
	return &Answers{
		Category: r.Category, CategoryConf: r.CategoryConf,
		StorageDelivery: r.StorageDelivery, ActionRequired: r.ActionRequired,
		Assay: r.Assay, AssayConf: r.AssayConf, Model: "llm:" + l.Model,
	}, nil
}
