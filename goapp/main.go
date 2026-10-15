// Yunzhun — bio-data intake app: mail -> judge -> pull, all-Go native UI.
// Menubar/tray app: icon toggles a compact panel; the full board opens on demand.
package main

import (
	"bytes"
	"database/sql"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"log"
	"math"
	"runtime"
	"sync"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"

	"goapp/internal/config"
	"goapp/internal/pipeline"
	"goapp/internal/store"
)

func icon(shapes string) *ui.SVG {
	return ui.MustParseSVG([]byte(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` + shapes + `</svg>`))
}

var (
	checkIcon   = icon(`<path d="M20 6 9 17l-5-5"/>`)
	xIcon       = icon(`<path d="M18 6 6 18"/><path d="m6 6 12 12"/>`)
	gearIcon    = icon(`<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>`)
	dnaIcon     = icon(`<path d="M4 3c0 3 2 5 4.5 5S16 6 16 9s-2.5 6-6 6-6 3-6 6"/><path d="M20 3c0 3-2 5-4.5 5S8 6 8 9s2.5 6 6 6 6 3 6 6"/><path d="M6.2 6.2h11.6"/><path d="M6.2 17.8h11.6"/><path d="M8 12h8"/>`)
	refreshIcon = icon(`<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>`)
	plusIcon    = icon(`<path d="M12 5v14"/><path d="M5 12h14"/>`)
	mailIcon    = icon(`<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 5L2 7"/>`)
	backIcon    = icon(`<path d="m15 18-6-6 6-6"/>`)
	boardIcon   = icon(`<rect x="3" y="3" width="7" height="18" rx="1"/><rect x="14" y="3" width="7" height="11" rx="1"/>`)
	downIcon    = icon(`<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>`)
)

func stageColor(t *ui.Theme, key string) ui.Color {
	switch key {
	case "pending":
		return t.Warning
	case "identified":
		return t.Accent
	case "downloaded":
		return t.Success
	case "failed":
		return t.Danger
	default:
		return t.TextMuted
	}
}

type column struct {
	key, title string
	color      ui.Color
	cards      []card
}

type card struct {
	MsgID     int64
	Subject   string
	From      string
	Delivery  float64
	Category  string
	Assay     string
	Stage     string
	Providers []string
	Done      int
	Failed    int
	Bytes     int64
}

type snapshot struct {
	cols      []column
	messages  int
	delivery  int
	filesDone int
	filesErr  int
	bytes     int64
}

type app struct {
	eng  *pipeline.Engine
	mu   sync.RWMutex
	snap snapshot

	win   *mygo.Window // full board
	panel *mygo.Window // menubar popover
	tray  *mygo.Tray
	quit  bool
	// panel sub-view: "" list | "detail" | "settings"
	pane     string
	selected *card
	// add-account form
	addr string
	code string
	msg  string
	f    map[string]*string
}

func stage(j *store.Judgment, done, failed int) string {
	if j == nil {
		return "pending"
	}
	if j.Category != "delivery" && j.StorageDelivery < 0.5 {
		return "other"
	}
	if failed > 0 {
		return "failed"
	}
	if done > 0 {
		return "downloaded"
	}
	return "identified"
}

func (a *app) refresh() {
	db := a.eng.DB
	rows, err := db.Query(`
		SELECT m.id, m.subject, m.from_addr,
		       j.category, j.storage_delivery, j.assay,
		       (SELECT COUNT(*) FROM pull_records p WHERE p.message_id=m.id AND p.status='done'),
		       (SELECT COUNT(*) FROM pull_records p WHERE p.message_id=m.id AND p.status='failed'),
		       (SELECT COALESCE(SUM(p.size),0) FROM pull_records p WHERE p.message_id=m.id AND p.status='done')
		FROM messages m LEFT JOIN judgments j ON j.message_id=m.id
		ORDER BY m.internal_date DESC NULLS LAST, m.id DESC LIMIT 200`)
	if err != nil {
		return
	}
	defer rows.Close()

	t := ui.LightTheme()
	cols := []column{
		{"pending", "待判定", stageColor(t, "pending"), nil},
		{"identified", "已识别交付", stageColor(t, "identified"), nil},
		{"downloaded", "已下载", stageColor(t, "downloaded"), nil},
		{"failed", "失败", stageColor(t, "failed"), nil},
		{"other", "其他邮件", stageColor(t, "other"), nil},
		{"analysis", "分析中", stageColor(t, "analysis"), nil},
		{"archived", "已归档", stageColor(t, "archived"), nil},
	}
	snap := snapshot{cols: cols}
	for rows.Next() {
		var c card
		var cat, assay sql.NullString
		var del sql.NullFloat64
		rows.Scan(&c.MsgID, &c.Subject, &c.From, &cat, &del, &assay,
			&c.Done, &c.Failed, &c.Bytes)
		c.Category = cat.String
		c.Delivery = del.Float64
		c.Assay = assay.String
		var j *store.Judgment
		if cat.Valid {
			j = &store.Judgment{Category: c.Category, StorageDelivery: c.Delivery, Assay: c.Assay}
		}
		c.Stage = stage(j, c.Done, c.Failed)
		refs, _ := db.Refs(c.MsgID)
		for _, r := range refs {
			c.Providers = append(c.Providers, r.Provider)
		}
		for i := range snap.cols {
			if snap.cols[i].key == c.Stage {
				snap.cols[i].cards = append(snap.cols[i].cards, c)
			}
		}
		snap.messages++
		if c.Stage == "identified" || c.Stage == "downloaded" || c.Stage == "failed" {
			snap.delivery++
		}
		snap.filesDone += c.Done
		snap.filesErr += c.Failed
		snap.bytes += c.Bytes
	}
	a.mu.Lock()
	a.snap = snap
	a.mu.Unlock()
}

func fmtBytes(n int64) string {
	if n == 0 {
		return "0 B"
	}
	u := []string{"B", "KB", "MB", "GB", "TB"}
	i := 0
	f := float64(n)
	for f >= 1024 && i < len(u)-1 {
		f /= 1024
		i++
	}
	return fmt.Sprintf("%.1f %s", f, u[i])
}

func orEmpty(s, d string) string {
	if s == "" {
		return d
	}
	return s
}

func chip(c *ui.Context, text string, clr ui.Color) ui.Element {
	t := c.Theme()
	return ui.Row(c).Padding(1, 5).Radius(4).Background(t.Background).Border(1, t.Border).Children(func() {
		ui.Text(c, text).FontSize(9).TextColor(clr)
	})
}

func iconBtn(c *ui.Context, ic *ui.SVG, tip string, fn func()) ui.Element {
	return ui.Button(c, "").Tooltip(tip).Children(func() {
		ui.Icon(c, ic).FontSize(14)
	}).OnClick(fn)
}

// ---------- full board window ----------

func (a *app) toolbar(c *ui.Context) {
	t := c.Theme()
	s := a.snap
	ui.Toolbar(c, func() {
		ui.Icon(c, dnaIcon).FontSize(18).TextColor(t.Accent)
		ui.Text(c, "Yunzhun").FontSize(16).Bold()
		ui.Text(c, "上游数据").FontSize(11).TextColor(t.TextMuted)
		ui.Spacer(c)
		stat := func(n int, label string, clr ui.Color) {
			ui.Row(c).Gap(4).AlignItems(ui.Center).Children(func() {
				ui.Textf(c, "%d", n).FontSize(12).Bold().TextColor(clr)
				ui.Text(c, label).FontSize(10).TextColor(t.TextMuted)
			})
		}
		stat(s.messages, "邮件", t.Text)
		stat(s.delivery, "交付", t.Accent)
		stat(s.filesDone, "文件", t.Success)
		if s.filesErr > 0 {
			stat(s.filesErr, "失败", t.Danger)
		}
		ui.Text(c, fmtBytes(s.bytes)).FontSize(11).TextColor(t.TextMuted).Padding(0, 8, 0, 0)
		iconBtn(c, refreshIcon, "立即同步", func() {
			go func() { a.eng.SyncAll(); a.eng.ProcessPending(); a.refresh() }()
		})
		iconBtn(c, gearIcon, "设置", func() { a.pane = "settings" })
	}).Padding(8, 16, 8)
}

func (a *app) accountBar(c *ui.Context) {
	ui.Row(c).Gap(8).AlignItems(ui.Center).Padding(10, 16, 10).Children(func() {
		ui.Icon(c, mailIcon).FontSize(14).TextColor(c.Theme().TextMuted)
		ui.TextInput(c, &a.addr).Placeholder("邮箱地址 xxx@163.com").Width(220)
		ui.TextInput(c, &a.code).Placeholder("授权码").Width(180)
		if ui.PrimaryButton(c, "添加账号").Clicked() && a.addr != "" && a.code != "" {
			prov := "163"
			if containsAny(a.addr, []string{"sysu", "qiye"}) {
				prov = "qiye163"
			}
			if _, err := a.eng.DB.UpsertAccount(a.addr, prov, a.code); err != nil {
				a.msg = err.Error()
			} else {
				a.msg = "已添加 " + a.addr
				go func() {
					a.eng.SyncAll()
					a.eng.ProcessPending()
					a.refresh()
				}()
				a.addr, a.code = "", ""
			}
		}
		if a.msg != "" {
			ui.Text(c, a.msg).FontSize(10).TextColor(c.Theme().Warning)
		}
	})
}

func containsAny(s string, subs []string) bool {
	for _, x := range subs {
		for i := 0; i+len(x) <= len(s); i++ {
			if s[i:i+len(x)] == x {
				return true
			}
		}
	}
	return false
}

func (a *app) cardView(c *ui.Context, cd card) ui.Element {
	t := c.Theme()
	return ui.Column(c).Gap(5).Padding(10).Radius(8).Background(t.Surface).
		Border(1, t.Border).Cursor(ui.CursorPointer).Children(func() {
		ui.Text(c, orEmpty(cd.Subject, "(无主题)")).FontSize(12).Bold()
		ui.Text(c, cd.From).FontSize(10).TextColor(t.TextMuted)
		if len(cd.Providers) > 0 || cd.Assay != "" {
			ui.Row(c).Gap(4).Wrap().Children(func() {
				for _, p := range cd.Providers {
					chip(c, p, t.Accent)
				}
				if cd.Assay != "" && cd.Assay != "other" {
					chip(c, cd.Assay, t.Success)
				}
			})
		}
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			if cd.Delivery > 0 {
				ui.Textf(c, "%.0f%%", cd.Delivery*100).FontSize(9).TextColor(t.TextMuted)
			}
			ui.Spacer(c)
			if cd.Done > 0 {
				ui.Row(c).Gap(3).AlignItems(ui.Center).Children(func() {
					ui.Icon(c, checkIcon).FontSize(10).TextColor(t.Success)
					ui.Textf(c, "%d", cd.Done).FontSize(9).TextColor(t.Success)
				})
			}
			if cd.Failed > 0 {
				ui.Row(c).Gap(3).AlignItems(ui.Center).Children(func() {
					ui.Icon(c, xIcon).FontSize(10).TextColor(t.Danger)
					ui.Textf(c, "%d", cd.Failed).FontSize(9).TextColor(t.Danger)
				})
			}
		})
	})
}

func (a *app) columnView(c *ui.Context, col column) ui.Element {
	t := c.Theme()
	disabled := col.key == "analysis" || col.key == "archived"
	el := ui.Column(c).Width(268).Padding(10).Gap(8).Radius(10).
		Background(t.Surface).Border(1, t.Border)
	if disabled {
		el = el.Opacity(0.4)
	}
	return el.Children(func() {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Padding(2).Children(func() {
			ui.Row(c).Size(8, 8).Radius(4).Background(col.color)
			ui.Text(c, col.title).FontSize(12).Bold()
			ui.Spacer(c)
			ui.Badge(c, fmt.Sprint(len(col.cards)))
		})
		ui.Scroll(c).Grow(1).Children(func() {
			ui.Column(c).Gap(6).Padding(1).Children(func() {
				for _, cd := range col.cards {
					cd := cd
					if el := a.cardView(c, cd); el.Clicked() {
						a.selected = &cd
						a.pane = "detail"
					}
				}
				if len(col.cards) == 0 {
					ui.Text(c, "—").FontSize(10).TextColor(t.TextMuted).Padding(8)
				}
			})
		})
	})
}

func (a *app) drawer(c *ui.Context) {
	t := c.Theme()
	cd := a.selected
	ui.Column(c).Width(320).Fill().Padding(16).Gap(10).Background(t.Surface).
		Border(1, t.Border).Children(func() {
		ui.Row(c).AlignItems(ui.Start).Children(func() {
			ui.Text(c, orEmpty(cd.Subject, "(无主题)")).FontSize(14).Bold().Grow(1)
			iconBtn(c, xIcon, "关闭", func() { a.pane = ""; a.selected = nil })
		})
		a.cardDetail(c, cd)
	})
}

func (a *app) cardDetail(c *ui.Context, cd *card) {
	t := c.Theme()
	ui.Text(c, cd.From).FontSize(11).TextColor(t.TextMuted)
	ui.Textf(c, "类别 %s · 交付 %.1f%% · 组学 %s",
		orEmpty(cd.Category, "—"), cd.Delivery*100, orEmpty(cd.Assay, "—")).
		FontSize(11).TextColor(t.Accent)
	ui.Text(c, "存储引用").FontSize(10).Bold().TextColor(t.TextMuted)
	refs, _ := a.eng.DB.Refs(cd.MsgID)
	for _, r := range refs {
		ui.Textf(c, "%s://%s/%s", r.Provider, r.Bucket, r.Key).
			Font("monospace").FontSize(10).TextColor(t.Accent)
	}
	ui.Text(c, "下载记录").FontSize(10).Bold().TextColor(t.TextMuted)
	rows, _ := a.eng.DB.Query(
		`SELECT remote_key, size, status, error FROM pull_records WHERE message_id=? ORDER BY id`, cd.MsgID)
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var k, st, er string
			var sz int64
			rows.Scan(&k, &sz, &st, &er)
			ic := checkIcon
			clr := t.Success
			if st != "done" {
				ic = xIcon
				clr = t.Danger
			}
			ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
				ui.Icon(c, ic).FontSize(11).TextColor(clr)
				ui.Textf(c, "%s (%s)", k, fmtBytes(sz)).Font("monospace").FontSize(10)
			})
			if er != "" {
				ui.Text(c, er).FontSize(9).TextColor(t.Danger)
			}
		}
	}
	ui.Spacer(c)
	if ui.PrimaryButton(c, "手动拉取").Clicked() {
		go func() {
			a.eng.PullMessage(cd.MsgID, true)
			a.refresh()
		}()
	}
}

func (a *app) settingsPanel(c *ui.Context) {
	t := c.Theme()
	if a.f == nil {
		a.f = map[string]*string{}
		eff := a.eng.EffectiveSettings()
		for _, k := range pipeline.SettingKeys {
			v := new(string)
			*v = eff[k]
			if k == "jev_key" && *v == "" {
				*v = a.eng.Cfg.JevAPIKey
			}
			if k == "llm_key" && *v == "" {
				*v = a.eng.Cfg.LLMKey
			}
			a.f[k] = v
		}
	}
	field := func(label, key, placeholder string) {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Text(c, label).FontSize(10).TextColor(t.TextMuted).Width(100)
			ui.TextInput(c, a.f[key]).Placeholder(placeholder).Grow(1)
		})
	}
	ui.Column(c).Fill().Padding(16).Gap(8).Children(func() {
		ui.Row(c).Children(func() {
			ui.Text(c, "设置").FontSize(14).Bold().Grow(1)
			iconBtn(c, xIcon, "关闭", func() { a.pane = "" })
		})
		ui.Scroll(c).Grow(1).Children(func() {
			ui.Column(c).Gap(8).Children(func() {
				ui.Text(c, "判定后端").FontSize(10).Bold().TextColor(t.Accent)
				field("provider", "jev_provider", "vercel | typesafe | llm")
				field("jev key", "jev_key", "vck_... 或 TYPESAFE key")
				ui.Text(c, "LLM 通道（provider=llm，兼兜底提取）").FontSize(10).Bold().TextColor(t.Accent)
				field("llm url", "llm_url", "openrouter.ai/api/v1/chat/completions")
				field("llm key", "llm_key", "sk-or-v1-...")
				field("llm model", "llm_model", "deepseek/deepseek-chat-v3-0324")
				ui.Text(c, "落盘与调度").FontSize(10).Bold().TextColor(t.Accent)
				field("下载目录", "download_dir", "./downloads 或 smb://u:p@host/share/dir")
				field("webhook", "webhook_url", "拉取成功 POST 通知（可空）")
				field("同步周期 s", "sync_interval", "120")
				field("pipeline s", "pipeline_interval", "60")
				field("IDLE", "idle_enabled", "true / false")
			})
		})
		if ui.PrimaryButton(c, "保存并生效").Clicked() {
			kv := map[string]string{}
			for _, k := range pipeline.SettingKeys {
				if p := a.f[k]; p != nil {
					kv[k] = *p
				}
			}
			a.eng.SaveSettings(kv)
			a.msg = "配置已保存并生效"
		}
	})
}

func (a *app) view(c *ui.Context) {
	c.SetTheme(ui.LightTheme())
	a.mu.RLock()
	snap := a.snap
	a.mu.RUnlock()

	ui.Column(c).Fill().Background(c.Theme().Background).Children(func() {
		a.toolbar(c)
		a.accountBar(c)
		ui.Row(c).Grow(1).AlignItems(ui.Stretch).Children(func() {
			ui.ScrollHorizontal(c).Grow(1).Padding(16).Children(func() {
				ui.Row(c).Gap(12).AlignItems(ui.Stretch).Children(func() {
					for _, col := range snap.cols {
						a.columnView(c, col)
					}
				})
			})
			switch a.pane {
			case "detail":
				if a.selected != nil {
					a.drawer(c)
				}
			case "settings":
				ui.Column(c).Width(380).Fill().Background(c.Theme().Surface).
					Border(1, c.Theme().Border).Children(func() {
					a.settingsPanel(c)
				})
			}
		})
	})
}

// ---------- menubar popover ----------

func (a *app) panelList(c *ui.Context) {
	t := c.Theme()
	s := a.snap
	ui.Column(c).Fill().Children(func() {
		ui.Row(c).Gap(6).AlignItems(ui.Center).Padding(10, 12, 6).Children(func() {
			ui.Icon(c, dnaIcon).FontSize(15).TextColor(t.Accent)
			ui.Text(c, "Yunzhun").FontSize(13).Bold()
			ui.Spacer(c)
			ui.Textf(c, "%d 交付 · %d 文件 · %s", s.delivery, s.filesDone, fmtBytes(s.bytes)).
				FontSize(10).TextColor(t.TextMuted)
		})
		ui.Divider(c)
		ui.Scroll(c).Grow(1).Children(func() {
			ui.Column(c).Gap(6).Padding(8).Children(func() {
				shown := 0
				for _, col := range s.cols[:5] { // real stages, skip placeholders
					for _, cd := range col.cards {
						if shown >= 30 {
							break
						}
						shown++
						cd := cd
						row := ui.Row(c).Gap(8).Padding(8).Radius(6).Background(t.Surface).
							Border(1, t.Border).Cursor(ui.CursorPointer).Children(func() {
							ui.Row(c).Size(6, 6).Radius(3).Background(col.color)
							ui.Column(c).Grow(1).Gap(2).Children(func() {
								ui.Text(c, orEmpty(cd.Subject, "(无主题)")).FontSize(11)
								ui.Row(c).Gap(6).Children(func() {
									ui.Text(c, col.title).FontSize(9).TextColor(t.TextMuted)
									if cd.Done+cd.Failed > 0 {
										ui.Textf(c, "%d✓/%d✗", cd.Done, cd.Failed).FontSize(9).TextColor(t.TextMuted)
									}
								})
							})
						})
						if row.Clicked() {
							a.selected = &cd
							a.pane = "detail"
						}
					}
				}
				if shown == 0 {
					ui.Text(c, "暂无邮件").FontSize(11).TextColor(t.TextMuted).Padding(16)
				}
			})
		})
		ui.Divider(c)
		ui.Row(c).Gap(4).AlignItems(ui.Center).Padding(8).Children(func() {
			iconBtn(c, refreshIcon, "立即同步", func() {
				go func() { a.eng.SyncAll(); a.eng.ProcessPending(); a.refresh() }()
			})
			iconBtn(c, gearIcon, "设置", func() { a.pane = "settings" })
			ui.Spacer(c)
			if ui.Button(c, "看板").Children(func() {
				ui.Icon(c, boardIcon).FontSize(12)
			}).Clicked() {
				a.showBoard()
			}
		})
	})
}

func (a *app) panelView(c *ui.Context) {
	c.SetTheme(ui.LightTheme())
	if c.Shortcut(0, ui.KeyEscape) || c.Shortcut(ui.Cmd, ui.KeyW) {
		a.panel.Hide()
	}
	t := c.Theme()
	ui.Column(c).Fill().Background(t.Background).Children(func() {
		switch a.pane {
		case "settings":
			a.settingsPanel(c)
		case "detail":
			ui.Column(c).Fill().Padding(12).Gap(8).Children(func() {
				ui.Row(c).AlignItems(ui.Center).Children(func() {
					iconBtn(c, backIcon, "返回", func() { a.pane = "" })
					ui.Text(c, "详情").FontSize(13).Bold()
				})
				if a.selected != nil {
					ui.Scroll(c).Grow(1).Children(func() {
						ui.Column(c).Gap(8).Children(func() {
							ui.Text(c, orEmpty(a.selected.Subject, "(无主题)")).FontSize(13).Bold()
							a.cardDetail(c, a.selected)
						})
					})
				}
			})
		default:
			a.panelList(c)
		}
	})
}

// ---------- tray ----------

func trayIcon() []byte {
	// a small DNA-ish glyph — template icon (macOS tints it black)
	img := image.NewNRGBA(image.Rect(0, 0, 44, 44))
	ink := color.NRGBA{A: 255}
	if runtime.GOOS != "darwin" {
		ink = color.NRGBA{R: 59, G: 130, B: 246, A: 255}
	}
	set := func(x, y int) {
		img.SetNRGBA(x, y, ink)
		img.SetNRGBA(x+1, y, ink)
		img.SetNRGBA(x, y+1, ink)
		img.SetNRGBA(x+1, y+1, ink)
	}
	for i := 0; i < 44; i++ {
		t := float64(i) / 44 * math.Pi * 2.6
		x1 := 12 + int(6*math.Sin(t))
		x2 := 44 - x1
		set(x1, i)
		set(x2, i)
		if i%7 == 3 {
			for x := x1 + 2; x < x2-1; x++ {
				set(x, i)
			}
		}
	}
	var b bytes.Buffer
	_ = png.Encode(&b, img)
	return b.Bytes()
}

// showPanel places the popover next to the tray icon.
func (a *app) showPanel() {
	anchor := a.tray.Bounds()
	if anchor.Width == 0 || anchor.Height == 0 {
		a.panel.Center()
	} else {
		area := mygo.Screen.DisplayNearestPoint(mygo.Point{
			X: anchor.X + anchor.Width/2, Y: anchor.Y + anchor.Height/2,
		}).WorkArea
		b := a.panel.Bounds()
		x := anchor.X + (anchor.Width-b.Width)/2
		y := anchor.Y + anchor.Height + 6
		if y+b.Height > area.Y+area.Height {
			y = anchor.Y - b.Height - 6
		}
		x = max(area.X, min(x, area.X+max(0, area.Width-b.Width)))
		y = max(area.Y, min(y, area.Y+max(0, area.Height-b.Height)))
		a.panel.SetPosition(x, y)
	}
	a.panel.Show()
}

func (a *app) togglePanel() {
	if a.panel.IsVisible() {
		a.panel.Hide()
	} else {
		a.pane = ""
		a.showPanel()
	}
}

func (a *app) showBoard() {
	if a.panel != nil {
		a.panel.Hide()
	}
	a.win.Show()
}

func (a *app) ready() {
	var err error
	a.tray, err = mygo.NewTray(mygo.TrayOptions{
		Icon:           trayIcon(),
		IconIsTemplate: true,
		ToolTip:        "Yunzhun · 上游数据",
	})
	if err != nil {
		log.Fatal("tray: ", err)
	}
	a.panel = mygo.NewWindow(mygo.WindowOptions{
		Title:             "Yunzhun",
		Width:             400,
		Height:            520,
		Hidden:            true,
		Frameless:         true,
		DisableResize:     true,
		DisableMinimize:   true,
		DisableMaximize:   true,
		DisableFullScreen: true,
		AlwaysOnTop:       true,
		SkipTaskbar:       true,
		Content:           ui.View(a.panelView),
	})
	a.panel.OnBlur(a.panel.Hide)
	a.panel.OnClose(func(e *mygo.CloseEvent) {
		if !a.quit {
			e.PreventDefault()
			a.panel.Hide()
		}
	})

	a.win = mygo.NewWindow(mygo.WindowOptions{
		Title:    "Yunzhun · 上游数据看板",
		Width:    1120,
		Height:   720,
		MinWidth: 860,
		MinHeight: 540,
		Hidden:   true,
		StateKey: "board",
		Content:  ui.View(a.view),
	})
	a.win.OnClose(func(e *mygo.CloseEvent) {
		if !a.quit {
			e.PreventDefault()
			a.win.Hide() // close to tray, not quit
		}
	})

	menu := mygo.NewMenu([]*mygo.MenuItem{
		{Label: "打开面板", Click: func(*mygo.MenuItem, *mygo.Window) { a.showPanel() }},
		{Label: "打开看板", Click: func(*mygo.MenuItem, *mygo.Window) { a.showBoard() }},
		mygo.Separator(),
		{Role: mygo.RoleQuit},
	})
	if runtime.GOOS == "linux" {
		a.tray.SetMenu(menu)
	} else {
		a.tray.OnClick(a.togglePanel)
		a.tray.OnRightClick(func() {
			a.panel.Hide()
			a.tray.SetMenu(menu)
			defer a.tray.SetMenu(nil)
			a.tray.PopUpMenu()
		})
	}
	mygo.App.OnWillQuit(func(*mygo.QuitEvent) {
		a.tray.Destroy()
	})
	// first run: nothing configured — open the board so the user sees it
	accts, _ := a.eng.DB.Accounts()
	if len(accts) == 0 {
		a.win.Show()
	}
}

func main() {
	cfg := config.Load()
	db, err := store.Open(cfg.DBPath)
	if err != nil {
		log.Fatal(err)
	}
	eng := pipeline.New(db, cfg)
	a := &app{eng: eng}
	a.refresh()

	eng.OnChange = func() {
		a.refresh()
		if a.win != nil {
			a.win.Invalidate()
		}
		if a.panel != nil {
			a.panel.Invalidate()
		}
	}
	eng.RunLoops(nil)

	mygo.App.SetName("Yunzhun")
	mygo.App.SetActivationPolicy(mygo.ActivationPolicyAccessory)
	mygo.App.OnBeforeQuit(func(*mygo.QuitEvent) { a.quit = true })
	mygo.App.WhenReady(a.ready)
	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
}
