package main

import (
	"os"
	"testing"

	"goapp/internal/config"
	"goapp/internal/mailx"
	"goapp/internal/pipeline"
	"goapp/internal/store"
)

func TestStorageRefExtraction(t *testing.T) {
	refs := mailx.ExtractStorageRefs(`
		数据交付位置：oss://novo-china-region/X101SC26023844-Z01/X101SC26023844-Z01-J039
		报表 https://bktdir.oss-cn-hangzhou.aliyuncs.com/db/dump.csv
		pan 链接 https://pan.sysu.edu.cn/share/abc123
	`)
	if len(refs) != 3 {
		t.Fatalf("want 3 refs, got %d", len(refs))
	}
	if refs[0].Provider != "aliyun-oss" || refs[0].Bucket != "novo-china-region" {
		t.Fatalf("bad oss scheme parse: %+v", refs[0])
	}
	if refs[2].Provider != "cloud-drive" {
		t.Fatalf("bad pan parse: %+v", refs[2])
	}
}

func TestChecksumExtraction(t *testing.T) {
	m := mailx.ExtractChecksums("d41d8cd98f00b204e9800998ecf8427e  sample_R1.fastq.gz\nmd5(report.tar) = 5d41402abc4b2a76b9719d911017c592")
	if m["sample_R1.fastq.gz"] != "d41d8cd98f00b204e9800998ecf8427e" {
		t.Fatal("md5-then-name form missed")
	}
	if m["report.tar"] != "5d41402abc4b2a76b9719d911017c592" {
		t.Fatal("md5(file)= form missed")
	}
}

func TestStoreRoundTrip(t *testing.T) {
	f, _ := os.CreateTemp("", "*.db")
	defer os.Remove(f.Name())
	f.Close()
	db, err := store.Open(f.Name())
	if err != nil {
		t.Fatal(err)
	}
	aid, _ := db.UpsertAccount("t@163.com", "163", "CODE")
	fid, _ := db.UpsertFolder(aid, "INBOX", "INBOX", 42)
	mid, err := db.InsertMessage(&store.Message{AccountID: aid, FolderID: fid, UID: 7, Subject: "hello"})
	if err != nil || mid == 0 {
		t.Fatalf("insert message: %v", err)
	}
	// same uid twice -> no dup
	if _, err := db.InsertMessage(&store.Message{AccountID: aid, FolderID: fid, UID: 7}); err != nil {
		t.Fatal(err)
	}
	pending, _ := db.PendingMessageIDs(10)
	if len(pending) != 1 {
		t.Fatalf("pending want 1, got %v", pending)
	}
	if err := db.SaveJudgment(&store.Judgment{MessageID: mid, Category: "delivery", StorageDelivery: 0.9}); err != nil {
		t.Fatal(err)
	}
	pending, _ = db.PendingMessageIDs(10)
	if len(pending) != 1 { // body still unfetched -> still pending
		t.Fatalf("pending after judge want 1, got %v", pending)
	}
}

func TestPipelineGate(t *testing.T) {
	f, _ := os.CreateTemp("", "*.db")
	defer os.Remove(f.Name())
	f.Close()
	db, _ := store.Open(f.Name())
	cfg := config.Load()
	cfg.DownloadDir = t.TempDir()
	eng := pipeline.New(db, cfg)
	aid, _ := db.UpsertAccount("g@163.com", "163", "C")
	fid, _ := db.UpsertFolder(aid, "INBOX", "INBOX", 1)
	mid, _ := db.InsertMessage(&store.Message{AccountID: aid, FolderID: fid, UID: 1, Subject: "x"})
	// no judgment -> gated
	if _, _, err := eng.PullMessage(mid, false); err == nil {
		t.Fatal("expected gate to block")
	}
	// force bypass still requires refs
	if _, _, err := eng.PullMessage(mid, true); err == nil {
		t.Fatal("expected no-refs error")
	}
}
