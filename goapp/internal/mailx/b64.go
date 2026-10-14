package mailx

import "encoding/base64"

func b64decode(s string) ([]byte, error) {
	return base64.StdEncoding.DecodeString(s)
}
