package config

import (
	_ "embed"
	"os"
	"strings"
)

//go:embed secrets.env
var secretsEnv string

// bundledEnv: defaults baked into the binary at build time (the file itself is
// gitignored — the key ships inside the app, never in source). Real env vars
// and DB settings still take precedence over these defaults.
var bundledEnv = parseEnv(secretsEnv)

func parseEnv(s string) map[string]string {
	out := map[string]string{}
	for _, line := range strings.Split(s, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if k, v, ok := strings.Cut(line, "="); ok {
			out[strings.TrimSpace(k)] = strings.TrimSpace(v)
		}
	}
	return out
}

// env resolves: real env var > bundled secret > def.
func envOrBundled(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	if v := bundledEnv[k]; v != "" {
		return v
	}
	return def
}
