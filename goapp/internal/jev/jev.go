// Package jev: TypeSafe System One judgment client (Vercel gateway or direct).
package jev

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	URL   string
	Model string
	Key   string
	http  *http.Client
}

func New(url, model, key string) *Client {
	return &Client{URL: url, Model: model, Key: key, http: &http.Client{Timeout: 20 * time.Second}}
}

var categories = map[string]string{
	"delivery":     "数据/文件交付（测序数据、报表、样品数据等，含下载地址或交付位置）",
	"billing":      "账单、账务、支付、扣款",
	"security":     "安全告警（异地登录、OAuth 授权变更、密码修改等）",
	"notification": "系统/平台通知（不含安全事件）",
	"personal":     "个人往来邮件",
	"other":        "以上都不是",
}

var actionLevels = []string{"无需任何行动", "可稍后处理（参阅/归档即可）", "需要尽快关注或处理"}

type Answers struct {
	Category        string
	CategoryConf    float64
	StorageDelivery float64
	ActionRequired  int
	Model           string
}

// Judge sends one batched SystemOne call: category + storage_delivery + action_required.
func (c *Client) Judge(state string) (*Answers, error) {
	payload := map[string]any{
		"model": c.Model,
		"state": state,
		"questions": map[string]any{
			"category": map[string]any{
				"type": "choice", "instructions": "这封邮件属于哪一类？", "criteria": categories,
			},
			"storage_delivery": map[string]any{
				"type":         "noul",
				"instructions": "这封邮件包含数据或文件的存储交付信息（下载地址、交付位置、网盘/OSS链接等）",
			},
			"action_required": map[string]any{
				"type": "score", "instructions": "收件人需要采取行动的紧迫程度", "criteria": actionLevels,
			},
		},
	}
	body, _ := json.Marshal(payload)
	req, _ := http.NewRequest("POST", c.URL, bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+c.Key)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("jev HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var out struct {
		Model   string `json:"model"`
		Answers map[string]struct {
			Choice     string  `json:"choice"`
			Confidence float64 `json:"confidence"`
			Noul       float64 `json:"noul"`
			Score      float64 `json:"score"`
		} `json:"answers"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("jev decode: %w", err)
	}
	a := &Answers{Model: out.Model}
	a.Category = out.Answers["category"].Choice
	a.CategoryConf = out.Answers["category"].Confidence
	a.StorageDelivery = out.Answers["storage_delivery"].Noul
	a.ActionRequired = int(out.Answers["action_required"].Score + 0.5)
	return a, nil
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
