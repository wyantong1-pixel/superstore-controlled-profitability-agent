"""从用户指定的 GitHub 仓库下载 CSV，不执行仓库代码、不覆盖已有文件。

先固定commit，再读取blob并核验Git对象SHA，保存来源与SHA256。
首次需要网络；数据来自历史样本，不是实时电商平台。使用/再分发前自行核实授权。
"""
import base64
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = 'wyantong1-pixel/superstore-ai-assisted-profitability-analysis'


def get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'superstore-agent-learning-demo'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    target = ROOT / 'data' / 'Sample - Superstore.csv'
    if target.exists():
        print('文件已存在，不覆盖：', target)
        return
    base = 'https://api.github.com/repos/' + REPO
    commit = get(base + '/commits/main')['sha']
    path = 'data/Sample - Superstore.csv'
    entry = get(base + '/contents/' + urllib.parse.quote(path) + '?ref=' + commit)
    blob = get(base + '/git/blobs/' + entry['sha'])
    content = base64.b64decode(blob['content'])
    expected = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    if expected != entry['sha']:
        raise RuntimeError('Git blob 校验失败，不保存数据。')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(content)
    manifest = {'repository': 'https://github.com/' + REPO, 'commit': commit, 'path': path,
                'git_blob': expected, 'sha256': hashlib.sha256(content).hexdigest(),
                'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
                'note': '用户已有项目中的静态样本；下载不表示获得额外再分发许可。'}
    (target.parent / 'source_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('已下载并验证：', target)


if __name__ == '__main__':
    main()

