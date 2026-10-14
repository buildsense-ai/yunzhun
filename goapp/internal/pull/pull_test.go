package pull

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLocalSinkWriteExistsDisplay(t *testing.T) {
	dir := t.TempDir()
	s, err := NewSink(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Write("msg-1/a/b.txt", []byte("hello")); err != nil {
		t.Fatal(err)
	}
	if !s.Exists("msg-1/a/b.txt") {
		t.Fatal("exists check failed")
	}
	if st, err := os.Stat(filepath.Join(dir, "msg-1/a/b.txt")); err != nil || st.Size() != 5 {
		t.Fatalf("file missing or wrong size: %v", err)
	}
	// atomic: no .part leftovers
	if _, err := os.Stat(filepath.Join(dir, "msg-1/a/b.txt.part")); !os.IsNotExist(err) {
		t.Fatal(".part file left behind")
	}
	// display is the absolute path
	disp := s.Display("msg-1/a/b.txt")
	if !filepath.IsAbs(disp) {
		t.Fatalf("display not absolute: %s", disp)
	}
	// traversal blocked
	if err := s.Write("../evil.txt", []byte("x")); err == nil {
		t.Fatal("traversal not blocked")
	}
}

func TestSMBURIParse(t *testing.T) {
	// unreachable host -> dial error, but proves URI parsing got past validation
	_, err := NewSink("smb://user:pass@nonexistent.invalid/share/dir")
	if err == nil {
		t.Fatal("expected dial error")
	}
	if got := err.Error(); !contains(got, "smb dial") {
		t.Fatalf("unexpected error: %v", err)
	}
	// missing share name -> parse error before any dial
	if _, err := NewSink("smb://host"); err == nil {
		t.Fatal("expected share-name error")
	}
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
