// Sink: where pulled objects land — local dir or an SMB share (smb:// URI).
// Both expose Write (atomic .part->rename), Exists (dedup check), Display.
package pull

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/hirochachacha/go-smb2"
)

type Sink interface {
	// Write persists data at rel ("/"-joined path under the sink root), atomically.
	Write(rel string, data []byte) error
	// Exists reports a non-empty file at rel.
	Exists(rel string) bool
	// Display renders rel as a human-facing path/URL.
	Display(rel string) string
}

func unsafeKey(key string) bool {
	return strings.Contains(key, "..") || strings.HasPrefix(key, "/") ||
		strings.ContainsRune(key, 0) || strings.ContainsRune(key, '\\')
}

// NewSink resolves a download root — local path or
// smb://[user[:pass]@]host[:port]/share[/sub/dir].
func NewSink(root string) (Sink, error) {
	if strings.HasPrefix(strings.ToLower(root), "smb://") {
		return newSMBSink(root)
	}
	return &localSink{base: root}, nil
}

// ---------- local ----------

type localSink struct{ base string }

func (s *localSink) Write(rel string, data []byte) error {
	target := filepath.Join(s.base, filepath.FromSlash(rel))
	abs, _ := filepath.Abs(target)
	absBase, _ := filepath.Abs(s.base)
	if !strings.HasPrefix(abs, absBase+string(os.PathSeparator)) {
		return fmt.Errorf("path escapes download dir: %q", rel)
	}
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	tmp := target + ".part"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, target)
}

func (s *localSink) Exists(rel string) bool {
	st, err := os.Stat(filepath.Join(s.base, filepath.FromSlash(rel)))
	return err == nil && st.Size() > 0
}

func (s *localSink) Display(rel string) string {
	abs, err := filepath.Abs(filepath.Join(s.base, filepath.FromSlash(rel)))
	if err != nil {
		return filepath.Join(s.base, filepath.FromSlash(rel))
	}
	return abs
}

// ---------- smb ----------

type smbSink struct {
	share    *smb2.Share
	prefix   string // sub-path inside the share ("" or "a/b")
	hostRoot string // smb://host/share for display
}

// newSMBSink parses smb://[user[:pass]@]host[:port]/share[/sub/dir] and opens a session.
func newSMBSink(raw string) (*smbSink, error) {
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("bad smb uri: %w", err)
	}
	if u.Host == "" {
		return nil, fmt.Errorf("smb uri needs host: %q", raw)
	}
	segs := strings.SplitN(strings.TrimPrefix(u.Path, "/"), "/", 2)
	if segs[0] == "" {
		return nil, fmt.Errorf("smb uri needs a share name: %q", raw)
	}
	share, prefix := segs[0], ""
	if len(segs) == 2 {
		prefix = strings.TrimSuffix(segs[1], "/")
	}

	user, pass := "", ""
	if u.User != nil {
		user = u.User.Username()
		pass, _ = u.User.Password()
	}
	host := u.Host
	if !strings.Contains(host, ":") {
		host += ":445"
	}
	conn, err := net.DialTimeout("tcp", host, 10*time.Second)
	if err != nil {
		return nil, fmt.Errorf("smb dial %s: %w", host, err)
	}
	d := &smb2.Dialer{Initiator: &smb2.NTLMInitiator{User: user, Password: pass}}
	sess, err := d.Dial(conn)
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("smb auth %s: %w", host, err)
	}
	fs, err := sess.Mount(share)
	if err != nil {
		sess.Logoff()
		conn.Close()
		return nil, fmt.Errorf("smb mount %s: %w", share, err)
	}
	return &smbSink{share: fs, prefix: prefix, hostRoot: fmt.Sprintf("smb://%s/%s", u.Host, share)}, nil
}

func (s *smbSink) relPath(rel string) string {
	if s.prefix == "" {
		return rel
	}
	return s.prefix + "/" + rel
}

func (s *smbSink) Write(rel string, data []byte) error {
	p := s.relPath(rel)
	if err := s.share.MkdirAll(path.Dir(p), 0o755); err != nil {
		return err
	}
	tmp := p + ".part"
	if err := s.share.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return s.share.Rename(tmp, p)
}

func (s *smbSink) Exists(rel string) bool {
	st, err := s.share.Stat(s.relPath(rel))
	return err == nil && st.Size() > 0
}

func (s *smbSink) Display(rel string) string {
	p := s.relPath(rel)
	return s.hostRoot + "/" + p
}
