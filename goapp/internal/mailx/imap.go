// IMAP client tuned for NetEase (RFC 2971 ID handshake + UID cursor sync).
package mailx

import (
	"bytes"
	"fmt"
	"time"

	"github.com/emersion/go-imap/v2"
	"github.com/emersion/go-imap/v2/imapclient"

	"goapp/internal/config"
)

type Client struct {
	c      *imapclient.Client
	PushCh chan struct{} // buffered(1): non-blocking notify on EXISTS/RECENT/etc
}

func Connect(acctAddress, authCode, provider string) (*Client, error) {
	p, ok := config.Providers[provider]
	if !ok {
		return nil, fmt.Errorf("unknown provider %q", provider)
	}
	cl := &Client{PushCh: make(chan struct{}, 1)}
	c, err := imapclient.DialTLS(fmt.Sprintf("%s:%d", p.IMAPHost, p.IMAPPort), &imapclient.Options{
		UnilateralDataHandler: &imapclient.UnilateralDataHandler{
			Mailbox: func(*imapclient.UnilateralDataMailbox) { cl.notify() },
			Expunge: func(uint32) { cl.notify() },
			Fetch:   func(*imapclient.FetchMessageData) { cl.notify() },
		},
	})
	if err != nil {
		return nil, fmt.Errorf("imap dial: %w", err)
	}
	if err := c.Login(acctAddress, authCode).Wait(); err != nil {
		c.Close()
		return nil, fmt.Errorf("imap login: %w", err)
	}
	if p.RequiresID {
		if _, err := c.ID(&imap.IDData{Name: "Yunzhun", Version: "0.2.0", Vendor: "BuildSense AI"}).Wait(); err != nil {
			c.Close()
			return nil, fmt.Errorf("imap ID rejected (NetEase requires it): %w", err)
		}
	}
	cl.c = c
	return cl, nil
}

func (cl *Client) notify() {
	select {
	case cl.PushCh <- struct{}{}:
	default:
	}
}

func (cl *Client) Close() { cl.c.Close() }

func (cl *Client) SupportsIdle() bool { return cl.c.Caps().Has(imap.CapIdle) }

// SelectMailbox switches the selected mailbox (for IDLE re-arming).
func (cl *Client) SelectMailbox(name string) error {
	_, err := cl.c.Select(name, nil).Wait()
	return err
}

// Idle blocks until the server pushes something or heartbeat elapses.
func (cl *Client) Idle(heartbeat time.Duration) (bool, error) {
	idle, err := cl.c.Idle()
	if err != nil {
		return false, err
	}
	select {
	case <-cl.PushCh:
		idle.Close()
		idle.Wait()
		return true, nil
	case <-time.After(heartbeat):
		idle.Close()
		idle.Wait()
		return false, nil
	}
}

type FolderStatus struct {
	UIDValidity uint32
	UIDNext     uint32
	Messages    int
	Unseen      int
}

type RemoteFolder struct {
	Name    string
	Decoded string
}

func (cl *Client) ListFolders() ([]RemoteFolder, error) {
	cmd := cl.c.List("", "*", nil)
	var out []RemoteFolder
	for {
		mbox := cmd.Next()
		if mbox == nil {
			break
		}
		out = append(out, RemoteFolder{Name: mbox.Mailbox, Decoded: DecodeUTF7(mbox.Mailbox)})
	}
	if err := cmd.Close(); err != nil {
		return nil, err
	}
	return out, nil
}

func (cl *Client) StatusFolder(name string) (*FolderStatus, error) {
	data, err := cl.c.Status(name, &imap.StatusOptions{
		UIDValidity: true, UIDNext: true, NumMessages: true, NumUnseen: true,
	}).Wait()
	if err != nil {
		return nil, err
	}
	st := &FolderStatus{UIDValidity: uint32(data.UIDValidity), UIDNext: uint32(data.UIDNext)}
	if data.NumMessages != nil {
		st.Messages = int(*data.NumMessages)
	}
	if data.NumUnseen != nil {
		st.Unseen = int(*data.NumUnseen)
	}
	return st, nil
}

type HeaderItem struct {
	UID          uint32
	Seen         bool
	InternalDate time.Time
	Size         int64
	Subject      string
	From         string
	MessageID    string
	Date         time.Time
}

// FetchHeadersSince: headers for messages with UID > sinceUID.
func (cl *Client) FetchHeadersSince(folder string, sinceUID uint32) ([]HeaderItem, error) {
	if _, err := cl.c.Select(folder, &imap.SelectOptions{ReadOnly: true}).Wait(); err != nil {
		return nil, err
	}
	crit := &imap.SearchCriteria{}
	var us imap.UIDSet
	us.AddRange(imap.UID(sinceUID+1), 0) // sinceUID+1:*
	crit.UID = []imap.UIDSet{us}
	data, err := cl.c.UIDSearch(crit, nil).Wait()
	if err != nil {
		return nil, err
	}
	set, ok := data.All.(imap.UIDSet)
	if !ok {
		return nil, nil
	}
	uids, _ := set.Nums()
	var list []imap.UID
	for _, u := range uids {
		if uint32(u) > sinceUID {
			list = append(list, u)
		}
	}
	if len(list) == 0 {
		return nil, nil
	}
	fetch := cl.c.Fetch(imap.UIDSetNum(list...), &imap.FetchOptions{
		UID: true, Flags: true, InternalDate: true, RFC822Size: true, Envelope: true,
	})
	var out []HeaderItem
	for {
		d := fetch.Next()
		if d == nil {
			break
		}
		buf, err := d.Collect()
		if err != nil {
			continue
		}
		it := HeaderItem{
			UID: uint32(buf.UID), InternalDate: buf.InternalDate, Size: buf.RFC822Size,
		}
		for _, f := range buf.Flags {
			if f == imap.FlagSeen {
				it.Seen = true
			}
		}
		if env := buf.Envelope; env != nil {
			it.Subject = env.Subject
			it.MessageID = env.MessageID
			it.Date = env.Date
			if len(env.From) > 0 {
				it.From = env.From[0].Addr()
			}
		}
		out = append(out, it)
	}
	return out, fetch.Close()
}

// FetchBody: full RFC822 for one UID.
func (cl *Client) FetchBody(folder string, uid uint32) ([]byte, error) {
	if _, err := cl.c.Select(folder, &imap.SelectOptions{ReadOnly: true}).Wait(); err != nil {
		return nil, err
	}
	section := &imap.FetchItemBodySection{Peek: true}
	fetch := cl.c.Fetch(imap.UIDSetNum(imap.UID(uid)), &imap.FetchOptions{
		UID: true, BodySection: []*imap.FetchItemBodySection{section},
	})
	var raw []byte
	for {
		d := fetch.Next()
		if d == nil {
			break
		}
		buf, err := d.Collect()
		if err != nil {
			continue
		}
		if b := buf.FindBodySection(section); b != nil {
			raw = b
		}
	}
	if err := fetch.Close(); err != nil {
		return nil, err
	}
	if raw == nil {
		return nil, fmt.Errorf("empty BODY[] for uid %d", uid)
	}
	return raw, nil
}

// DecodeUTF7: IMAP modified UTF-7 (NetEase Chinese folders like &XfJj0-).
func DecodeUTF7(s string) string {
	var out bytes.Buffer
	for i := 0; i < len(s); {
		if s[i] != '&' {
			out.WriteByte(s[i])
			i++
			continue
		}
		j := i + 1
		for j < len(s) && s[j] != '-' {
			j++
		}
		if j == i+1 { // "&-" is a literal "&"
			out.WriteByte('&')
			i = j + 1
			continue
		}
		b64 := s[i+1 : j]
		for k := 0; k < len(b64); k++ {
			if b64[k] == ',' {
				b64 = b64[:k] + "/" + b64[k+1:]
			}
		}
		if b, err := b64decode(b64); err == nil {
			for k := 0; k+1 < len(b); k += 2 {
				out.WriteRune(rune(b[k])<<8 | rune(b[k+1]))
			}
		} else {
			out.WriteString(s[i : j+1])
		}
		i = j + 1
	}
	return out.String()
}
