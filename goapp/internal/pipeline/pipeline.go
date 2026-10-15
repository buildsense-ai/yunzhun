// Package pipeline: the event-driven chain — sync -> judge -> gated pull.
package pipeline

import (
	"fmt"
	"log"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/emersion/go-imap/v2"
	_ "github.com/emersion/go-imap/v2/imapclient"

	"goapp/internal/config"
	"goapp/internal/jev"
	"goapp/internal/mailx"
	"goapp/internal/pull"
	"goapp/internal/store"
)

type judger interface {
	Judge(state string) (*jev.Answers, error)
}

type Engine struct {
	DB       *store.DB
	Cfg      config.Settings
	jev      judger
	llm      *jev.LLM
	failures map[int64]int
	mu       sync.Mutex
	// OnChange fires after state changes (new mail, judgment, pull) — UI hook.
	OnChange func()
	stopCh chan struct{}
}

// SettingKeys: app-level configurable settings (DB overrides env defaults).
var SettingKeys = []string{
	"jev_provider", "jev_url", "jev_model", "jev_key",
	"llm_url", "llm_model", "llm_key",
	"download_dir", "webhook_url",
	"sync_interval", "pipeline_interval", "pipeline_batch",
	"idle_enabled", "idle_folder",
}

func New(db *store.DB, cfg config.Settings) *Engine {
	e := &Engine{DB: db, Cfg: cfg, failures: map[int64]int{}, stopCh: make(chan struct{})}
	e.applyDbOverrides()
	e.buildClients()
	return e
}
// applyDbOverrides: DB settings beat env defaults.
func (e *Engine) applyDbOverrides() {
	kv, err := e.DB.AllSettings()
	if err != nil {
		return
	}
	e.ApplyMap(kv)
}

// ApplyMap merges a settings map into the live config (no persistence here).
func (e *Engine) ApplyMap(kv map[string]string) {
	c := &e.Cfg
	if v := kv["jev_provider"]; v != "" { c.JevProvider = v }
	if v := kv["jev_url"]; v != "" { c.JevURL = v }
	if v := kv["jev_model"]; v != "" { c.JevModel = v }
	if v := kv["jev_key"]; v != "" { c.JevAPIKey = v }
	if v := kv["llm_url"]; v != "" { c.LLMURL = v }
	if v := kv["llm_model"]; v != "" { c.LLMModel = v }
	if v := kv["llm_key"]; v != "" { c.LLMKey = v }
	if v := kv["download_dir"]; v != "" { c.DownloadDir = v }
	if v := kv["webhook_url"]; v != "" { c.WebhookURL = v }
	if v := kv["idle_folder"]; v != "" { c.IdleFolder = v }
	if v := kv["idle_enabled"]; v != "" { c.IdleEnabled = v == "true" }
	if n := atoi(kv["sync_interval"]); n > 0 { c.SyncInterval = time.Duration(n) * time.Second }
	if n := atoi(kv["pipeline_interval"]); n > 0 { c.PipelineInterval = time.Duration(n) * time.Second }
	if n := atoi(kv["pipeline_batch"]); n > 0 { c.PipelineBatch = n }
}

func atoi(s string) int {
	n := 0
	for _, ch := range s {
		if ch < '0' || ch > '9' { return 0 }
		n = n*10 + int(ch-'0')
	}
	return n
}

func (e *Engine) buildClients() {
	if e.Cfg.LLMModel != "" && e.Cfg.LLMKey != "" {
		e.llm = jev.NewLLM(e.Cfg.LLMURL, e.Cfg.LLMModel, e.Cfg.LLMKey)
	}
	switch e.Cfg.JevProvider {
	case "llm":
		if e.llm != nil {
			e.jev = e.llm
		}
	default: // vercel | typesafe
		if e.Cfg.JevAPIKey != "" {
			e.jev = jev.New(e.Cfg.JevURL, e.Cfg.JevModel, e.Cfg.JevAPIKey)
		}
	}
	// auto-fallback: no working jev key but an llm channel exists -> judge via LLM
	if e.jev == nil && e.llm != nil {
		e.jev = e.llm
	}
}

// EffectiveSettings: current values for the settings form (secrets masked as saved marker).
func (e *Engine) EffectiveSettings() map[string]string {
	c := e.Cfg
	return map[string]string{
		"jev_provider": c.JevProvider, "jev_url": c.JevURL, "jev_model": c.JevModel,
		"llm_url": c.LLMURL, "llm_model": c.LLMModel,
		"download_dir": c.DownloadDir, "webhook_url": c.WebhookURL,
		"idle_folder": c.IdleFolder, "idle_enabled": boolStr(c.IdleEnabled),
		"sync_interval": itoa(int(c.SyncInterval / time.Second)),
		"pipeline_interval": itoa(int(c.PipelineInterval / time.Second)),
		"pipeline_batch": itoa(c.PipelineBatch),
	}
}

// SaveSettings persists kv and applies immediately (clients rebuilt, loops restarted).
func (e *Engine) SaveSettings(kv map[string]string) {
	for _, k := range SettingKeys {
		if v, ok := kv[k]; ok {
			e.DB.SetSetting(k, v)
		}
	}
	e.ApplyMap(kv)
	e.buildClients()
	e.RestartLoops()
}

func boolStr(b bool) string { if b { return "true" }; return "false" }
func itoa(n int) string {
	if n == 0 { return "0" }
	var b [12]byte
	i := len(b)
	for n > 0 { i--; b[i] = byte('0' + n%10); n /= 10 }
	return string(b[i:])
}

func (e *Engine) changed() {
	if e.OnChange != nil {
		e.OnChange()
	}
}

// ---------- sync ----------

func (e *Engine) SyncAccount(acct store.Account) (int, error) {
	cl, err := mailx.Connect(acct.Address, acct.AuthCode, acct.Provider)
	if err != nil {
		e.DB.SetAccountError(acct.ID, err.Error())
		return 0, err
	}
	defer cl.Close()

	remote, err := cl.ListFolders()
	if err != nil {
		return 0, err
	}
	newTotal := 0
	for _, rf := range remote {
		st, err := cl.StatusFolder(rf.Name)
		if err != nil {
			log.Printf("status %s: %v", rf.Name, err)
			continue
		}
		existing, _ := e.DB.Folders(acct.ID)
		var fid int64
		var lastUID uint32
		var oldValidity int64 = -1
		for _, f := range existing {
			if f.Name == rf.Name {
				fid, lastUID, oldValidity = f.ID, f.LastSeenUID, f.UIDValidity
				break
			}
		}
		fid, err = e.DB.UpsertFolder(acct.ID, rf.Name, rf.Decoded, int64(st.UIDValidity))
		if err != nil {
			continue
		}
		if oldValidity >= 0 && oldValidity != int64(st.UIDValidity) {
			e.DB.WipeFolder(fid)
			lastUID = 0
		}
		items, err := cl.FetchHeadersSince(rf.Name, lastUID)
		if err != nil {
			log.Printf("fetch headers %s: %v", rf.Name, err)
			continue
		}
		for _, it := range items {
			e.DB.InsertMessage(&store.Message{
				AccountID: acct.ID, FolderID: fid, UID: it.UID,
				MessageID: it.MessageID, Subject: it.Subject, FromAddr: it.From,
				Date: it.Date, InternalDate: it.InternalDate, Size: it.Size,
				Seen: it.Seen,
			})
			if it.UID > lastUID {
				lastUID = it.UID
			}
			newTotal++
		}
		e.DB.UpdateFolderCursor(fid, lastUID, int64(st.UIDValidity))
	}
	e.DB.SetAccountError(acct.ID, "")
	return newTotal, nil
}

func hasFlag(flags []imap.Flag, want string) bool {
	for _, f := range flags {
		if string(f) == want {
			return true
		}
	}
	return false
}

func (e *Engine) SyncAll() int {
	accts, err := e.DB.Accounts()
	if err != nil {
		return 0
	}
	total := 0
	for _, a := range accts {
		n, err := e.SyncAccount(a)
		if err != nil {
			log.Printf("sync %s: %v", a.Address, err)
			continue
		}
		total += n
	}
	if total > 0 {
		e.changed()
	}
	return total
}

// ---------- body / extract ----------

func (e *Engine) EnsureBody(msgID int64) (*store.Message, error) {
	m, err := e.DB.Message(msgID)
	if err != nil {
		return nil, err
	}
	if m.BodyFetched && len(m.Raw) > 0 {
		return m, nil
	}
	folder, err := e.DB.FolderByID(m.FolderID)
	if err != nil {
		return nil, err
	}
	acct, err := e.accountOf(m.AccountID)
	if err != nil {
		return nil, err
	}
	cl, err := mailx.Connect(acct.Address, acct.AuthCode, acct.Provider)
	if err != nil {
		return nil, err
	}
	defer cl.Close()
	raw, err := cl.FetchBody(folder.Name, m.UID)
	if err != nil {
		return nil, err
	}
	parsed, _ := mailx.ParseMessage(raw)
	m.Raw = raw
	m.TextBody = parsed.Text
	m.HTMLBody = parsed.HTML
	if parsed.Snippet != "" {
		m.Snippet = parsed.Snippet
	}
	m.Size = int64(len(raw))
	m.BodyFetched = true
	if err := e.DB.SaveBody(m); err != nil {
		return nil, err
	}
	e.extractAndStore(m)
	return m, nil
}

func (e *Engine) extractAndStore(m *store.Message) {
	checksums := mailx.ExtractChecksums(m.TextBody, m.HTMLBody)
	refs := mailx.ExtractStorageRefs(m.TextBody, m.HTMLBody)
	rows := make([]store.ObjectRef, 0, len(refs))
	for _, r := range refs {
		base := r.Key
		if i := strings.LastIndex(base, "/"); i >= 0 {
			base = base[i+1:]
		}
		rows = append(rows, store.ObjectRef{
			AccountID: m.AccountID, Provider: r.Provider, Bucket: r.Bucket,
			Key: r.Key, Region: r.Region, URL: r.URL, Presigned: r.Presigned,
			Source: "regex", ExpectedMD5: checksums[base],
		})
	}
	e.DB.ReplaceRefs(m.ID, rows)
}

// ---------- judge ----------

func (e *Engine) Judge(msgID int64) (*store.Judgment, error) {
	if e.jev == nil {
		return nil, fmt.Errorf("no judgment provider configured")
	}
	m, err := e.EnsureBody(msgID)
	if err != nil {
		return nil, err
	}
	refs, _ := e.DB.Refs(m.ID)
	var sb strings.Builder
	fmt.Fprintf(&sb, "主题: %s\n发件人: %s\n", m.Subject, m.FromAddr)
	for _, r := range refs {
		fmt.Fprintf(&sb, "已识别的存储地址: %s://%s/%s\n", r.Provider, r.Bucket, r.Key)
	}
	body := m.TextBody
	if len(body) > 2000 {
		body = body[:2000]
	}
	fmt.Fprintf(&sb, "正文:\n%s", body)
	state := sb.String()
	if len(state) > 4000 {
		state = state[:4000]
	}

	ans, err := e.jev.Judge(state)
	if err != nil {
		return nil, err
	}
	j := &store.Judgment{
		MessageID: m.ID, Category: ans.Category, CategoryConf: ans.CategoryConf,
		StorageDelivery: ans.StorageDelivery, ActionRequired: ans.ActionRequired,
		Assay: ans.Assay, AssayConf: ans.AssayConf, Model: ans.Model,
	}
	if err := e.DB.SaveJudgment(j); err != nil {
		return nil, err
	}

	// step 3: delivery-ish + regex empty -> LLM fallback
	if e.llm != nil && j.StorageDelivery >= 0.5 && len(refs) == 0 {
		e.llmFallback(m)
	}
	return j, nil
}

func (e *Engine) llmFallback(m *store.Message) {
	text := m.Subject + "\n" + m.TextBody + "\n" + m.HTMLBody
	addrs, err := e.llm.ExtractAddresses(text)
	if err != nil {
		return
	}
	checksums := mailx.ExtractChecksums(m.TextBody, m.HTMLBody)
	var rows []store.ObjectRef
	for _, a := range addrs {
		r := mailx.Classify(a)
		if r == nil {
			continue
		}
		base := r.Key
		if i := strings.LastIndex(base, "/"); i >= 0 {
			base = base[i+1:]
		}
		rows = append(rows, store.ObjectRef{
			AccountID: m.AccountID, Provider: r.Provider, Bucket: r.Bucket,
			Key: r.Key, Region: r.Region, URL: r.URL, Presigned: r.Presigned,
			Source: "llm", ExpectedMD5: checksums[base],
		})
	}
	if len(rows) > 0 {
		existing, _ := e.DB.Refs(m.ID)
		e.DB.ReplaceRefs(m.ID, append(existing, rows...))
	}
}

// ---------- pull ----------

func (e *Engine) PullMessage(msgID int64, force bool) (downloaded int, skipped []string, err error) {
	j, _ := e.DB.Judgment(msgID)
	if !force {
		if j == nil {
			return 0, nil, fmt.Errorf("no judgment yet")
		}
		if j.Category != "delivery" && j.StorageDelivery < 0.5 {
			return 0, nil, fmt.Errorf("gated: category=%s storage_delivery=%.2f", j.Category, j.StorageDelivery)
		}
	}
	refs, err := e.DB.Refs(msgID)
	if err != nil || len(refs) == 0 {
		return 0, nil, fmt.Errorf("no storage refs")
	}
	doneSet := map[string]bool{}
	if keys, err := e.DB.DoneKeys(msgID); err == nil {
		for k := range keys {
			doneSet[k] = true
		}
	}
	sink, err := pull.NewSink(e.Cfg.DownloadDir)
	if err != nil {
		return 0, nil, err
	}
	// lab naming convention: <YYYY-M-D>/<assay-dir>/  (e.g. 2026-9-28/10X/)
	m, _ := e.DB.Message(msgID)
	date := time.Now()
	if m != nil {
		if !m.InternalDate.IsZero() {
			date = m.InternalDate
		} else if !m.Date.IsZero() {
			date = m.Date
		}
	}
	// Jev's semantic verdict first; keyword classifier as fallback
	assay := "other"
	if j != nil && j.Assay != "" && j.Assay != "other" {
		assay = j.Assay
	} else if m != nil {
		assay = mailx.AssayDir(m.Subject, m.TextBody)
	}
	base := fmt.Sprintf("%s/%s", date.Format("2006-1-2"), assay)

	for i := range refs {
		ref := refs[i]
		st, _ := e.DB.FindStore(ref.Provider, ref.Bucket)
		pulled, failures, trunc := pull.PullRef(st, &ref, sink, base, true, 200, doneSet)
		for _, disp := range pulled {
			// remote key = display path minus the sink's prefix display
			e.DB.UpsertPullRecord(&store.PullRecord{
				MessageID: msgID, Provider: ref.Provider, Bucket: ref.Bucket,
				RemoteKey: keyFromDisp(disp, sink, base), LocalPath: disp, Status: "done",
			})
			downloaded++
		}
		for _, fail := range failures {
			key := strings.SplitN(fail, ": ", 2)[0]
			e.DB.UpsertPullRecord(&store.PullRecord{
				MessageID: msgID, Provider: ref.Provider, Bucket: ref.Bucket,
				RemoteKey: key, Status: "failed", Error: fail,
			})
			skipped = append(skipped, fail)
		}
		if trunc {
			skipped = append(skipped, "prefix listing truncated")
		}
		if st == nil && len(pulled) == 0 && len(failures) == 0 {
			skipped = append(skipped, ref.Provider+"/"+ref.Bucket+": no registered store")
		}
	}
	return downloaded, skipped, nil
}

// keyFromDisp recovers the remote key from a display path produced by the sink.
// Local displays are absolute paths; smb displays are smb:// URLs — in both
// cases the remote key is everything after "msg-<id>/".
func keyFromDisp(disp string, sink pull.Sink, base string) string {
	marker := base + "/"
	if i := strings.Index(disp, marker); i >= 0 {
		return disp[i+len(marker):]
	}
	return filepath.Base(disp)
}

// ---------- orchestration ----------

const maxAttempts = 5

func (e *Engine) ProcessPending() (judged, pulled int) {
	ids, err := e.DB.PendingMessageIDs(e.Cfg.PipelineBatch)
	if err != nil {
		return
	}
	for _, id := range ids {
		e.mu.Lock()
		if e.failures[id] >= maxAttempts {
			e.mu.Unlock()
			continue
		}
		e.mu.Unlock()
		j, err := e.Judge(id)
		if err != nil {
			e.mu.Lock()
			e.failures[id]++
			e.mu.Unlock()
			log.Printf("judge %d: %v", id, err)
			continue
		}
		e.mu.Lock()
		delete(e.failures, id)
		e.mu.Unlock()
		judged++
		if j.Category == "delivery" || j.StorageDelivery >= 0.5 {
			n, skipped, err := e.PullMessage(id, false)
			pulled += n
			if err != nil {
				log.Printf("pull %d: %v", id, err)
			}
			for _, s := range skipped {
				log.Printf("pull %d skipped: %s", id, s)
			}
		}
		time.Sleep(300 * time.Millisecond)
	}
	if judged+pulled > 0 {
		e.changed()
	}
	if pulled > 0 {
		e.notifyWebhook()
	}
	return
}

func (e *Engine) notifyWebhook() {
	if e.Cfg.WebhookURL == "" {
		return
	}
	resp, err := http.Post(e.Cfg.WebhookURL, "application/json",
		strings.NewReader(`{"event":"pull.completed"}`))
	if err == nil {
		resp.Body.Close()
	}
}

// ---------- background loops ----------

func (e *Engine) RunLoops(stop <-chan struct{}) {
	e.mu.Lock()
	e.stopCh = make(chan struct{})
	st := e.stopCh
	e.mu.Unlock()
	go func() { // pipeline sweeper
		t := time.NewTicker(e.Cfg.PipelineInterval)
		defer t.Stop()
		for {
			select {
			case <-st:
				return
			case <-t.C:
				e.ProcessPending()
			}
		}
	}()
	go func() { // polling sync
		t := time.NewTicker(e.Cfg.SyncInterval)
		defer t.Stop()
		for {
			select {
			case <-st:
				return
			case <-t.C:
				if n := e.SyncAll(); n > 0 {
					e.ProcessPending() // event-driven kick
				}
			}
		}
	}()
	if e.Cfg.IdleEnabled {
		go e.idleSupervisor(st)
	}
}

// RestartLoops stops the current background loops and starts fresh ones,
// picking up interval/provider changes.
func (e *Engine) RestartLoops() {
	e.mu.Lock()
	if e.stopCh != nil {
		close(e.stopCh)
	}
	e.mu.Unlock()
	e.RunLoops(nil)
}

func (e *Engine) idleSupervisor(stop <-chan struct{}) {
	workers := map[int64]chan struct{}{}
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		accts, _ := e.DB.Accounts()
		alive := map[int64]bool{}
		for _, a := range accts {
			alive[a.ID] = true
			if _, ok := workers[a.ID]; !ok {
				wstop := make(chan struct{})
				workers[a.ID] = wstop
				go e.idleWorker(a, wstop)
			}
		}
		for id, w := range workers {
			if !alive[id] {
				close(w)
				delete(workers, id)
			}
		}
		select {
		case <-stop:
			for _, w := range workers {
				close(w)
			}
			return
		case <-t.C:
		}
	}
}

func (e *Engine) idleWorker(acct store.Account, stop <-chan struct{}) {
	backoff := 5 * time.Second
	for {
		select {
		case <-stop:
			return
		default:
		}
		cl, err := mailx.Connect(acct.Address, acct.AuthCode, acct.Provider)
		if err != nil {
			log.Printf("idle %s: %v", acct.Address, err)
			select {
			case <-stop:
				return
			case <-time.After(backoff):
			}
			if backoff < 5*time.Minute {
				backoff *= 2
			}
			continue
		}
		backoff = 5 * time.Second
		if !cl.SupportsIdle() {
			cl.Close()
			return // polling loop covers this account
		}
		cl.SelectMailbox(e.Cfg.IdleFolder)
		for {
			got, err := cl.Idle(e.Cfg.IdleHeartbeat)
			if err != nil {
				break // reconnect
			}
			if got {
				e.SyncAccount(acct)
				e.ProcessPending()
				cl.SelectMailbox(e.Cfg.IdleFolder)
			}
		}
		cl.Close()
	}
}

func (e *Engine) accountOf(id int64) (*store.Account, error) {
	accts, err := e.DB.Accounts()
	if err != nil {
		return nil, err
	}
	for _, a := range accts {
		if a.ID == id {
			return &a, nil
		}
	}
	return nil, fmt.Errorf("account %d not found", id)
}
