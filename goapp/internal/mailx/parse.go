// MIME parsing tuned for real-world 163 mail (GBK/gb2312 legacy charsets).
package mailx

import (
	"bytes"
	"io"
	"mime"
	"regexp"
	"strings"
	"time"

	"github.com/emersion/go-message"
	"github.com/emersion/go-message/mail"
	"golang.org/x/text/encoding/simplifiedchinese"
	"golang.org/x/text/encoding/traditionalchinese"
	"golang.org/x/text/transform"
)

func init() {
	// go-message decodes part bodies through this global hook — route
	// GBK/gb2312/big5 through x/text so legacy NetEase mail parses.
	message.CharsetReader = charsetReader
}

func charsetReader(charset string, r io.Reader) (io.Reader, error) {
	switch strings.ToLower(strings.TrimSpace(charset)) {
	case "gbk", "gb2312", "gb18030":
		return transform.NewReader(r, simplifiedchinese.GBK.NewDecoder()), nil
	case "big5":
		return transform.NewReader(r, traditionalchinese.Big5.NewDecoder()), nil
	default:
		return transform.NewReader(r, simplifiedchinese.GBK.NewDecoder()), nil // GBK superset covers utf-8-unsafe legacy
	}
}

var wordDecoder = &mime.WordDecoder{CharsetReader: func(cs string, r io.Reader) (io.Reader, error) {
	return charsetReader(cs, r)
}}

type Parsed struct {
	MessageID   string
	Subject     string
	From        string
	Date        time.Time
	Text        string
	HTML        string
	Snippet     string
}

var tagRe = regexp.MustCompile(`<[^>]+>`)
var wsRe = regexp.MustCompile(`\s+`)

func makeSnippet(text, html string) string {
	src := strings.TrimSpace(text)
	if src == "" {
		src = strings.TrimSpace(tagRe.ReplaceAllString(html, " "))
	}
	return truncate(wsRe.ReplaceAllString(src, " "), 240)
}

func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

// ParseMessage: full RFC822 -> text/html/snippet. Headers use a GBK-aware
// word decoder via mime.WordDecoder (go-message supports it through
// mail.NewReaderWithHeader? we decode manually).
func ParseMessage(raw []byte) (*Parsed, error) {
	mr, err := mail.CreateReader(bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	defer mr.Close()

	h := mr.Header
	p := &Parsed{
		Subject:   decodeHeader(h.Get("Subject")),
		MessageID: strings.Trim(h.Get("Message-ID"), "<>"),
		From:      decodeHeader(h.Get("From")),
	}
	if t, err := h.Date(); err == nil {
		p.Date = t
	}

	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			break
		}
		ctype := part.Header.Get("Content-Type")
		b, _ := io.ReadAll(part.Body)
		lower := strings.ToLower(ctype)
		switch {
		case strings.HasPrefix(lower, "text/plain") && p.Text == "":
			p.Text = string(b)
		case strings.HasPrefix(lower, "text/html") && p.HTML == "":
			p.HTML = string(b)
		}
	}
	p.Snippet = makeSnippet(p.Text, p.HTML)
	return p, nil
}

// decodeHeader: RFC 2047 with GBK support.
func decodeHeader(s string) string {
	if s == "" {
		return ""
	}
	if out, err := wordDecoder.DecodeHeader(s); err == nil {
		return out
	}
	return s
}
