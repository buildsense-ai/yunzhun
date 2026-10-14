// Yunzhun — bio-data intake app: mail -> judge -> pull, all-Go native UI.
package main

import (
	"database/sql"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"

	"goapp/internal/config"
	"goapp/internal/pipeline"
	"goapp/internal/store"
)

var (
	bgDeep   = ui.Hex("#070d0b")
	bgPanel  = ui.Hex("#0d1512")
	edge     = ui.Hex("#1c2b25")
	inkMain  = ui.Hex("#d7e5de")
	inkDim   = ui.Hex("#6b857c")
	cEmerald = ui.Hex("#34d399")
	cTeal    = ui.Hex("#2dd4bf")
	cAmber   = ui.Hex("#fbbf24")
	cRose    = ui.Hex("#fb7185")
	cSlate   = ui.Hex("#64748b")
	cLime    = ui.Hex("#a3e635")
)

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
	eng      *pipeline.Engine
	mu       sync.RWMutex
	snap     snapshot
	selected *card
	// add-account form
	addr string
	code string
	msg  string
}

func stage(m store.Message, j *store.Judgment, done, failed int) string {
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
		SELECT m.id, m.subject, m.from_addr, m.internal_date,
		       j.category, j.storage_delivery,
		       (SELECT COUNT(*) FROM pull_records p WHERE p.message_id=m.id AND p.status='done'),
		       (SELECT COUNT(*) FROM pull_records p WHERE p.message_id=m.id AND p.status='failed'),
		       (SELECT COALESCE(SUM(p.size),0) FROM pull_records p WHERE p.message_id=m.id AND p.status='done')
		FROM messages m LEFT JOIN judgments j ON j.message_id=m.id
		ORDER BY m.internal_date DESC NULLS LAST, m.id DESC LIMIT 200`)
	if err != nil {
		return
	}
	defer rows.Close()

	cols := []column{
		{"pending", "待判定", cAmber, nil},
		{"identified", "已识别交付", cTeal, nil},
		{"downloaded", "已下载", cEmerald, nil},
		{"failed", "失败", cRose, nil},
		{"other", "其他邮件", cSlate, nil},
		{"analysis", "分析中", cLime, nil},
		{"archived", "已归档", cLime, nil},
	}
	snap := snapshot{}
	for rows.Next() {
		var c card
		var cat sql.NullString
		var del sql.NullFloat64
		var idate sql.NullTime
		rows.Scan(&c.MsgID, &c.Subject, &c.From, &idate, &cat, &del, &c.Done, &c.Failed, &c.Bytes)
		c.Category = cat.String
		c.Delivery = del.Float64
		var j *store.Judgment
		if cat.Valid {
			j = &store.Judgment{Category: c.Category, StorageDelivery: c.Delivery}
		}
		st := stage(store.Message{}, j, c.Done, c.Failed)
		refs, _ := db.Refs(c.MsgID)
		for _, r := range refs {
			c.Providers = append(c.Providers, r.Provider)
		}
		for i := range cols {
			if cols[i].key == st {
				cols[i].cards = append(cols[i].cards, c)
			}
		}
		snap.messages++
		if st == "identified" || st == "downloaded" || st == "failed" {
			snap.delivery++
		}
		snap.filesDone += c.Done
		snap.filesErr += c.Failed
		snap.bytes += c.Bytes
	}
	a.mu.Lock()
	a.snap = snapshot{cols: cols, messages: snap.messages, delivery: snap.delivery,
		filesDone: snap.filesDone, filesErr: snap.filesErr, bytes: snap.bytes}
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

func (a *app) header(c *ui.Context) {
	ui.Row(c).Fill().Padding(16).AlignItems(ui.Center).Gap(14).Children(func() {
		ui.Text(c, "🧬 Yunzhun").FontSize(18).Bold().TextColor(cEmerald)
		ui.Text(c, "上游数据看板").FontSize(13).TextColor(inkDim)
		ui.Spacer(c)
		s := a.snap
		ui.Textf(c, "邮件 %d", s.messages).FontSize(12).TextColor(inkDim)
		ui.Textf(c, "交付 %d", s.delivery).FontSize(12).TextColor(cTeal)
		ui.Textf(c, "文件 %d", s.filesDone).FontSize(12).TextColor(cEmerald)
		if s.filesErr > 0 {
			ui.Textf(c, "失败 %d", s.filesErr).FontSize(12).TextColor(cRose)
		}
		ui.Textf(c, "%s", fmtBytes(s.bytes)).FontSize(12).TextColor(inkDim)
	})
}

func (a *app) cardView(c *ui.Context, col column, cd card) {
	ui.Column(c).Fill().Padding(10).Gap(4).Background(bgPanel).
		Border(1, edge).Radius(8).Children(func() {
		ui.Text(c, cd.Subject).FontSize(13)
		ui.Text(c, cd.From).FontSize(10).TextColor(inkDim)
		if len(cd.Providers) > 0 {
			ui.Row(c).Gap(4).Children(func() {
				for _, p := range cd.Providers {
					ui.Text(c, p).FontSize(9).TextColor(cTeal).Padding(2).
						Background(bgDeep).Radius(4)
				}
			})
		}
		ui.Row(c).Gap(8).Children(func() {
			if cd.Delivery > 0 {
				ui.Textf(c, "交付 %.0f%%", cd.Delivery*100).FontSize(9).TextColor(inkDim)
			}
			if cd.Done > 0 {
				ui.Textf(c, "%d✓", cd.Done).FontSize(9).TextColor(cEmerald)
			}
			if cd.Failed > 0 {
				ui.Textf(c, "%d✗", cd.Failed).FontSize(9).TextColor(cRose)
			}
		})
	})
}

func (a *app) columnView(c *ui.Context, col column) {
	disabled := col.key == "analysis" || col.key == "archived"
	el := ui.Column(c).Width(280).Padding(10).Gap(8).Background(bgPanel).
		Border(1, edge).Radius(10)
	if disabled {
		el = el.Opacity(0.35)
	}
	el.Children(func() {
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Text(c, "●").FontSize(9).TextColor(col.color)
			ui.Text(c, col.title).FontSize(13).Bold()
			ui.Spacer(c)
			ui.Textf(c, "%d", len(col.cards)).FontSize(11).TextColor(inkDim)
		})
		ui.Scroll(c).Grow(1).Children(func() {
			ui.Column(c).Gap(6).Children(func() {
				for _, cd := range col.cards {
					cd := cd
					if ui.Button(c, "").Children(func() {
						a.cardView(c, col, cd)
					}).Clicked() {
						a.selected = &cd
					}
				}
				if len(col.cards) == 0 {
					ui.Text(c, "—").FontSize(10).TextColor(inkDim).Padding(8)
				}
			})
		})
	})
}

func (a *app) drawer(c *ui.Context) {
	if a.selected == nil {
		return
	}
	cd := a.selected
	ui.Column(c).Width(320).Fill().Padding(16).Gap(8).Background(bgPanel).
		Border(1, edge).Children(func() {
		ui.Row(c).Children(func() {
			ui.Text(c, cd.Subject).FontSize(14).Bold().Grow(1)
			if ui.Button(c, "✕").Clicked() {
				a.selected = nil
			}
		})
		ui.Text(c, cd.From).FontSize(11).TextColor(inkDim)
		ui.Textf(c, "类别 %s · 交付概率 %.1f%%", cd.Category, cd.Delivery*100).FontSize(11).TextColor(cTeal)
		ui.Text(c, "存储引用").FontSize(10).Bold().TextColor(inkDim)
		refs, _ := a.eng.DB.Refs(cd.MsgID)
		for _, r := range refs {
			ui.Textf(c, "%s://%s/%s", r.Provider, r.Bucket, r.Key).FontSize(10).TextColor(cTeal)
		}
		ui.Text(c, "下载记录").FontSize(10).Bold().TextColor(inkDim)
		rows, _ := a.eng.DB.Query(`SELECT remote_key, size, status, error FROM pull_records WHERE message_id=?`, cd.MsgID)
		if rows != nil {
			defer rows.Close()
			for rows.Next() {
				var k, st, er string
				var sz int64
				rows.Scan(&k, &sz, &st, &er)
				icon := "✓"
				clr := cEmerald
				if st != "done" {
					icon = "✗"
					clr = cRose
				}
				ui.Textf(c, "%s %s (%s)", icon, k, fmtBytes(sz)).FontSize(10).TextColor(clr)
				if er != "" {
					ui.Text(c, er).FontSize(9).TextColor(cRose)
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
	})
}

func (a *app) accountBar(c *ui.Context) {
	ui.Row(c).Fill().Padding(12).Gap(8).AlignItems(ui.Center).Background(bgPanel).
		Border(1, edge).Children(func() {
		ui.TextInput(c, &a.addr).Placeholder("邮箱地址 xxx@163.com").Width(220)
		ui.TextInput(c, &a.code).Placeholder("授权码").Width(180)
		if ui.PrimaryButton(c, "添加账号").Clicked() && a.addr != "" && a.code != "" {
			prov := "163"
			if len(a.addr) > 5 && a.addr[len(a.addr)-4:] == ".com" && containsAny(a.addr, []string{"sysu", "qiye"}) {
				prov = "qiye163"
			}
			if _, err := a.eng.DB.UpsertAccount(a.addr, prov, a.code); err != nil {
				a.msg = err.Error()
			} else {
				a.msg = "已添加 " + a.addr
				go func() {
					a.eng.SyncAll()
					a.eng.ProcessPending()
				}()
				a.addr, a.code = "", ""
			}
		}
		if ui.Button(c, "立即同步").Clicked() {
			go func() {
				a.eng.SyncAll()
				a.eng.ProcessPending()
			}()
		}
		if a.msg != "" {
			ui.Text(c, a.msg).FontSize(10).TextColor(cAmber)
		}
	})
}

func containsAny(s string, subs []string) bool {
	for _, x := range subs {
		if len(s) >= len(x) {
			for i := 0; i+len(x) <= len(s); i++ {
				if s[i:i+len(x)] == x {
					return true
				}
			}
		}
	}
	return false
}

func (a *app) view(c *ui.Context) {
	c.SetTheme(bioTheme())
	a.mu.RLock()
	a.snap = a.snap // read under lock
	snap := a.snap
	a.mu.RUnlock()
	a.snap = snap

	ui.Column(c).Fill().Background(bgDeep).Children(func() {
		a.header(c)
		a.accountBar(c)
		ui.Row(c).Grow(1).Children(func() {
			ui.ScrollHorizontal(c).Grow(1).Padding(16).Children(func() {
				ui.Row(c).Gap(12).AlignItems(ui.Start).Children(func() {
					for _, col := range snap.cols {
						a.columnView(c, col)
					}
				})
			})
			a.drawer(c)
		})
	})
}

func bioTheme() *ui.Theme {
	t := ui.DarkTheme()
	t.Background = bgDeep
	t.Surface = bgPanel
	t.Text = inkMain
	t.TextMuted = inkDim
	t.Accent = cEmerald
	return t
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

	stop := make(chan struct{})
	var win *mygo.Window
	eng.OnChange = func() {
		a.refresh()
		if win != nil {
			win.Invalidate()
		}
	}
	eng.RunLoops(stop)

	mygo.App.WhenReady(func() {
		win = mygo.NewWindow(mygo.WindowOptions{
			Title:     "Yunzhun · 上游数据",
			Width:     1280,
			Height:    800,
			MinWidth:  900,
			MinHeight: 600,
			StateKey:  "main",
			Content:   ui.View(a.view),
		})
	})
	if err := mygo.App.Run(); err != nil {
		log.Fatal(err)
	}
	close(stop)
}

var _ = time.Second // heartbeat default kept in config
