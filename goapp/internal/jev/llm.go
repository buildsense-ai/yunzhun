// LLM fallback extraction: only when Jev flags delivery AND regex found nothing.
package jev

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type LLM struct {
	URL   string
	Model string
	Key   string
	http  *http.Client
}

func NewLLM(url, model, key string) *LLM {
	return &LLM{URL: url, Model: model, Key: key, http: &http.Client{Timeout: 30 * time.Second}}
}

const extractPrompt = `You extract file-storage addresses from email text. Respond with JSON only: {"addresses": ["<full address>"]}. Normalize prose-form storage descriptions (e.g. 'bucket: foo, prefix: bar/x') into proper URIs (oss://bucket/key, obs://bucket/key, cos://bucket/key, s3://bucket/key, or https:// URLs). Only include real storage/download addresses; never ordinary web links.`

// ExtractAddresses asks the LLM to normalize prose-form addresses into URIs.
func (l *LLM) post(payload map[string]any) ([]byte, error) {
	body, _ := json.Marshal(payload)
	req, _ := http.NewRequest("POST", l.URL, bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+l.Key)
	req.Header.Set("Content-Type", "application/json")
	resp, err := l.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("llm HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	return raw, nil
}

func (l *LLM) ExtractAddresses(text string) ([]string, error) {
	raw, err := l.post(map[string]any{
		"model": l.Model,
		"messages": []map[string]string{
			{"role": "system", "content": extractPrompt},
			{"role": "user", "content": truncate(text, 6000)},
		},
		"response_format": map[string]string{"type": "json_object"},
	})
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
		return nil, fmt.Errorf("llm decode: %v", err)
	}
	var parsed struct {
		Addresses []string `json:"addresses"`
	}
	content := out.Choices[0].Message.Content
	if i := bytes.Index([]byte(content), []byte("{")); i >= 0 {
		content = content[i:]
	}
	if err := json.Unmarshal([]byte(content), &parsed); err != nil {
		return nil, nil // unparseable = no addresses, fallback is best-effort
	}
	return parsed.Addresses, nil
}
