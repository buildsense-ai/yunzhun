// Package config: env-driven settings for the Yunzhun app.
package config

import (
	"os"
	"strconv"
	"time"
)

type Provider struct {
	Name       string
	IMAPHost   string
	IMAPPort   int
	POP3Host   string
	POP3Port   int
	SMTPHost   string
	SMTPPort   int
	RequiresID bool // NetEase requires RFC 2971 ID after login
}

var Providers = map[string]Provider{
	"163":     {"163", "imap.163.com", 993, "pop.163.com", 995, "smtp.163.com", 465, true},
	"126":     {"126", "imap.126.com", 993, "pop.126.com", 995, "smtp.126.com", 465, true},
	"qiye163": {"qiye163", "imaphz.qiye.163.com", 993, "pophz.qiye.163.com", 995, "smtphz.qiye.163.com", 465, true},
}

type Settings struct {
	DBPath          string
	DownloadDir     string
	SyncInterval    time.Duration
	PipelineInterval time.Duration
	PipelineBatch   int
	IdleEnabled     bool
	IdleFolder      string
	IdleHeartbeat   time.Duration

	// Jev (TypeSafe System One) via Vercel AI Gateway or direct
	JevProvider  string // "vercel" | "typesafe"
	JevURL       string
	JevModel     string
	JevAPIKey    string

	// LLM fallback extraction (OpenAI-compatible, same gateway)
	LLMModel  string
	LLMKey    string
	LLMURL    string

	// outbound webhook after auto-pulls
	WebhookURL  string
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envDur(k string, def time.Duration) time.Duration {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return time.Duration(n) * time.Second
		}
	}
	return def
}

func Load() Settings {
	s := Settings{
		DBPath:           env("YUNZHUN_DB", "yunzhun.db"),
		DownloadDir:      env("YUNZHUN_DOWNLOAD_DIR", "./downloads"),
		SyncInterval:     envDur("YUNZHUN_SYNC_INTERVAL_SECONDS", 120*time.Second),
		PipelineInterval: envDur("YUNZHUN_PIPELINE_INTERVAL_SECONDS", 60*time.Second),
		PipelineBatch:    20,
		IdleEnabled:      env("YUNZHUN_IDLE_ENABLED", "true") == "true",
		IdleFolder:       env("YUNZHUN_IDLE_FOLDER", "INBOX"),
		IdleHeartbeat:    envDur("YUNZHUN_IDLE_HEARTBEAT_SECONDS", 1500*time.Second),
		JevProvider:      env("YUNZHUN_JEV_PROVIDER", "vercel"),
		JevModel:         env("YUNZHUN_JEV_MODEL", ""),
		LLMModel:         env("YUNZHUN_LLM_MODEL", ""),
		WebhookURL:       env("YUNZHUN_WEBHOOK_URL", ""),
	}
	if n := os.Getenv("YUNZHUN_PIPELINE_BATCH_LIMIT"); n != "" {
		if v, err := strconv.Atoi(n); err == nil {
			s.PipelineBatch = v
		}
	}
	// provider-specific Jev endpoints
	gwKey := env("YUNZHUN_VERCEL_GATEWAY_KEY", env("AI_GATEWAY_API_KEY", ""))
	if s.JevProvider == "typesafe" {
		s.JevURL = "https://api.typesafe.ai/v1/systemone"
		if s.JevModel == "" {
			s.JevModel = "jev-latest"
		}
		s.JevAPIKey = env("YUNZHUN_JEV_API_KEY", os.Getenv("TYPESAFE_API_KEY"))
	} else {
		s.JevURL = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
		if s.JevModel == "" {
			s.JevModel = "typesafe-ai/jev"
		}
		s.JevAPIKey = gwKey
	}
	s.LLMURL = env("YUNZHUN_LLM_URL", "https://ai-gateway.vercel.app/v1/chat/completions")
	s.LLMKey = env("YUNZHUN_LLM_KEY", env("YUNZHUN_LLM_API_KEY", gwKey))
	return s
}
