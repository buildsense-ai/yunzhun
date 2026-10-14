// Vendor pullers via the official pure-Go SDKs (no cgo anywhere).
package pull

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	alioss "github.com/aliyun/aliyun-oss-go-sdk/oss"
	cos "github.com/tencentyun/cos-go-sdk-v5"
	obs "github.com/huaweicloud/huaweicloud-sdk-go-obs/obs"
	minio "github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"

	"goapp/internal/store"
)

// ---------- Aliyun OSS ----------

type ossPuller struct{ b *alioss.Bucket }

func newOSS(st *store.Store) (*ossPuller, error) {
	ep := st.Endpoint
	if ep == "" {
		ep = fmt.Sprintf("https://oss-%s.aliyuncs.com", st.Region)
	}
	cli, err := alioss.New(ep, st.AccessKey, st.SecretKey)
	if err != nil {
		return nil, err
	}
	b, err := cli.Bucket(st.Bucket)
	if err != nil {
		return nil, err
	}
	return &ossPuller{b}, nil
}

func (p *ossPuller) Read(key string) ([]byte, error) {
	rc, err := p.b.GetObject(key)
	if err != nil {
		return nil, err
	}
	defer rc.Close()
	return io.ReadAll(rc)
}

func (p *ossPuller) Stat(key string) (int64, error) {
	h, err := p.b.GetObjectMeta(key)
	if err != nil {
		return 0, err
	}
	var n int64
	fmt.Sscan(h.Get("Content-Length"), &n)
	return n, nil
}

func (p *ossPuller) List(prefix string) (files, dirs []string, err error) {
	res, err := p.b.ListObjects(alioss.Prefix(prefix), alioss.Delimiter("/"), alioss.MaxKeys(500))
	if err != nil {
		return nil, nil, err
	}
	for _, o := range res.Objects {
		files = append(files, o.Key)
	}
	return files, res.CommonPrefixes, nil
}

// ---------- Tencent COS ----------

type cosPuller struct{ c *cos.Client }

func newCOS(st *store.Store) (*cosPuller, error) {
	u, _ := url.Parse(fmt.Sprintf("https://%s.cos.%s.myqcloud.com", st.Bucket, st.Region))
	c := cos.NewClient(&cos.BaseURL{BucketURL: u}, &http.Client{
		Timeout: 10 * time.Minute,
		Transport: &cos.AuthorizationTransport{
			SecretID: st.AccessKey, SecretKey: st.SecretKey,
		},
	})
	return &cosPuller{c}, nil
}

func (p *cosPuller) Read(key string) ([]byte, error) {
	resp, err := p.c.Object.Get(context.Background(), key, nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

func (p *cosPuller) Stat(key string) (int64, error) {
	resp, err := p.c.Object.Head(context.Background(), key, nil)
	if err != nil {
		return 0, err
	}
	return resp.ContentLength, nil
}

func (p *cosPuller) List(prefix string) (files, dirs []string, err error) {
	res, _, err := p.c.Bucket.Get(context.Background(), &cos.BucketGetOptions{
		Prefix: prefix, Delimiter: "/", MaxKeys: 500,
	})
	if err != nil {
		return nil, nil, err
	}
	for _, o := range res.Contents {
		files = append(files, o.Key)
	}
	return files, res.CommonPrefixes, nil
}

// ---------- Huawei OBS ----------

type obsPuller struct {
	c      *obs.ObsClient
	bucket string
}

func newOBS(st *store.Store) (*obsPuller, error) {
	ep := st.Endpoint
	if ep == "" {
		ep = fmt.Sprintf("https://obs.%s.myhuaweicloud.com", st.Region)
	}
	cli, err := obs.New(st.AccessKey, st.SecretKey, ep)
	if err != nil {
		return nil, err
	}
	return &obsPuller{cli, st.Bucket}, nil
}

func (p *obsPuller) Read(key string) ([]byte, error) {
	out, err := p.c.GetObject(&obs.GetObjectInput{Bucket: p.bucket, Key: key})
	if err != nil {
		return nil, err
	}
	defer out.Body.Close()
	return io.ReadAll(out.Body)
}

func (p *obsPuller) Stat(key string) (int64, error) {
	out, err := p.c.GetObjectMetadata(&obs.GetObjectMetadataInput{Bucket: p.bucket, Key: key})
	if err != nil {
		return 0, err
	}
	return out.ContentLength, nil
}

func (p *obsPuller) List(prefix string) (files, dirs []string, err error) {
	out, err := p.c.ListObjects(&obs.ListObjectsInput{
		Bucket: p.bucket, Prefix: prefix, Delimiter: "/", MaxKeys: 500,
	})
	if err != nil {
		return nil, nil, err
	}
	for _, o := range out.Contents {
		files = append(files, o.Key)
	}
	return files, out.CommonPrefixes, nil
}

// ---------- AWS S3 / S3-compatible (minio) ----------

type s3Puller struct {
	c      *minio.Client
	bucket string
}

func newS3(st *store.Store) (*s3Puller, error) {
	ep := st.Endpoint
	if ep == "" {
		ep = fmt.Sprintf("s3.%s.amazonaws.com", st.Region)
	}
	host := strings.TrimPrefix(strings.TrimPrefix(ep, "https://"), "http://")
	c, err := minio.New(host, &minio.Options{
		Creds:  credentials.NewStaticV4(st.AccessKey, st.SecretKey, ""),
		Secure: strings.HasPrefix(ep, "https") || st.Endpoint == "",
		Region: st.Region,
	})
	if err != nil {
		return nil, err
	}
	return &s3Puller{c, st.Bucket}, nil
}

func (p *s3Puller) Read(key string) ([]byte, error) {
	obj, err := p.c.GetObject(context.Background(), p.bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return nil, err
	}
	defer obj.Close()
	return io.ReadAll(obj)
}

func (p *s3Puller) Stat(key string) (int64, error) {
	info, err := p.c.StatObject(context.Background(), p.bucket, key, minio.StatObjectOptions{})
	if err != nil {
		return 0, err
	}
	return info.Size, nil
}

func (p *s3Puller) List(prefix string) (files, dirs []string, err error) {
	for obj := range p.c.ListObjects(context.Background(), p.bucket, minio.ListObjectsOptions{
		Prefix: prefix, Recursive: false, MaxKeys: 500,
	}) {
		if obj.Err != nil {
			return files, dirs, obj.Err
		}
		if strings.HasSuffix(obj.Key, "/") {
			dirs = append(dirs, obj.Key)
		} else {
			files = append(files, obj.Key)
		}
	}
	return files, dirs, nil
}
