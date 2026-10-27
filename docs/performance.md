# 性能測定と使い分け

以下は0.9／0.10候補の履歴測定です。0.11.0を再測定した値ではありません。

「Rustだから常に速い」とは表現しません。HTTP transport、Python処理、テンプレート、
I/O待ち、接続数で結果が変わります。Flask風APIや標準装備の利便性と、速度は別に評価します。

## 再現方法

### 0.10候補: async経路のON/OFF比較

同じ0.10.0候補コードのasync JSON/20ms待機routeを、fast pathのON/OFFだけ切り替えて測定しました。
Starletteも同じUvicorn/asyncio/h11で比較しています。Windows/Python/CPUは後述の環境と同じで、
各1.5秒＋warmup 0.3秒、3round、順序をseed付きで入れ替えました。
54測定ケースとwarmupで、通信エラー・不正応答は0でした。
[全round・遅延・CPU/RSSの生データ](assets/benchmarks/asgi-async-windows-2026-10-27.json)を参照してください。
source SHA-256は`fc10515fb4b63c6ba63b11cdbf97e88014c987cb82372d29de1dc4ef5d2679d1`です。

JSONの中央値（req/s）:

| 方式 | c=1 | c=16 | c=64 | c=16 p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| Higuma bridge（OFF） | 1,298 | 1,699 | 1,685 | 10.41 |
| Higuma fast（ON） | 3,281 | 4,146 | 4,408 | 4.24 |
| Starlette | 5,568 | 7,704 | 7,553 | 2.37 |

対象のasync JSONではc=16で約2.44倍になり、p95も改善しました。ただしStarletteのthroughputの
約54%であり、まだ差があります。同期middleware/hooks/DI等を使うrouteは自動fallbackするため、
この改善を適用できない場合があります。これは従来のsync JSONを測った0.9の表とは別のworkloadです。

20ms模擬I/O・c=64はbridge 1,357、fast 1,495、Starlette 1,629 req/sでした。
待機中心ではJSONほどの効果がなく、3roundのmin/maxも重なります。DBや実networkでの改善率を保証しません。
ASGI同時処理枠64はON/OFF両方に残しています。native/WSGIの0.10再測定ではありません。

```bash
python -m bench.async_paths --output target/asgi-async-new.json --rounds 3 --duration 1.5 --concurrency 1 16 64
```

### 通常の5方式比較

```bash
python -m pip install -e ".[benchmark,asgi,compat]"
python -m bench.suite --output target/benchmark-new.json --rounds 3 --duration 1.5 --concurrency 1 16
```

既存outputは上書きせず、新しいfilenameを指定します。既存の`bench/compare.py`は
開発サーバーとの簡易比較で、結論の根拠には使いません。

比較対象はHiguma native、Higuma＋Uvicorn、Higuma＋Waitress、Flask＋Waitress、
Starlette＋Uvicornです。単一server process・HTTP/1.1 keep-alive、Uvicornはasyncio/h11、
Waitressは16 threads・接続上限512、nativeのTokio I/O workerは1、Higuma ASGI調整worker上限は64です。
設定が同じ意味ではないので、単一processを「同一内部並列性」とは解釈しません。

5種類のGET workload（小さいJSON、typed path/query、20msの模擬I/O、Python CPU処理、
20要素のHTML）をwarmup後、同時接続1/16で3回ずつ計測します。
8MiB uploadは各方式・各roundで5requestだけ送信し、無制限のdisk負荷を避けます。
Flask/Starletteも逐次入力を使い、相手だけ全量bufferする条件にしていません。

JSON値・HTTP status・HTML要素数・uploadサイズを検査し、エラーと不正応答を別に記録します。
出力には生のround別データ、median/min/max throughput、p50/p95/p99、server/client CPU、
RSSのbaseline/peak、Python/依存版、OS/CPU/RAM、Git commit/dirty状態、source hashを含めます。

## Windows実測（0.9候補・2026-10-27の記録）

以下は0.9候補時点の履歴であり、0.10の測定値とは混ぜません。該当実装はcommit `62e04a5`に含まれます。

Windows 11 build 26200、CPython 3.12.9、20 physical / 28 logical CPU、RAM約31.7GiB。
Higuma 0.9.0候補をrelease buildし、Flask 3.1.3、Starlette 1.10.0、
Uvicorn 0.54.0、Waitress 3.0.2と比較しました。Starletteの入力上限も32MiBへ設定しています。
ベースcommitは`9a8e1d0`、未commitの候補コードが測定対象です。
測定対象ソースSHA-256は`fa06251c306c4ea88cb9013e871a0296ff3f6f12d263f4f778974ce626848281`です。
OS/依存版・全round・同時接続1の結果も含む[生データ](assets/benchmarks/windows-2026-10-27.json)を公開します。

次は同時接続16、各1.5秒×3roundの中央値（正しい応答のみのreq/s）です。

| 方式 | JSON | path/query | Python CPU | HTML template |
| --- | ---: | ---: | ---: | ---: |
| Higuma native | 11,692 | 11,374 | 1,749 | 11,801 |
| Higuma / Uvicorn | 1,908 | 1,832 | 962 | 1,863 |
| Higuma / Waitress | 5,279 | 4,789 | 1,460 | 4,928 |
| Flask / Waitress | 5,983 | 5,510 | 1,481 | 4,988 |
| Starlette / Uvicorn | 7,549 | 7,187 | 1,571 | 6,478 |

JSON・同時接続16の遅延と資源も分けて掲載します。p95とCPUは3roundの中央値、
RSSは当該ケースの観測peak最大値です。CPUの1.0は約1core相当で、PC全体の100%ではありません。

| 方式 | p95 (ms) | RSS peak (MiB) | server CPU (core相当) |
| --- | ---: | ---: | ---: |
| Higuma native | 2.75 | 36.00 | 1.38 |
| Higuma / Uvicorn | 9.31 | 51.89 | 1.08 |
| Higuma / Waitress | 5.03 | 40.06 | 1.18 |
| Flask / Waitress | 4.48 | 46.17 | 1.13 |
| Starlette / Uvicorn | 2.48 | 41.98 | 1.00 |

この条件ではnativeのJSON throughputはStarletteの約1.55倍でしたが、p95とCPU消費は
Starletteが良好でした。Higuma ASGIはJSON throughputがStarletteの約25%で、
bridge/worker経由のコストが残ります。Flask風APIを維持したままASGIへ配備できますが、
ASGI-native frameworkと同じ効率を保証するものではありません。
Higuma WSGIもこのJSON workloadではFlaskを上回りませんでした。
Python CPU処理がRust実装へ自動変換されるわけでもありません。

8MiB uploadは各方式15request（5×3）の小規模確認です。全量展開を避けた逐次読取が動くことを
確認しましたが、長期・並列upload耐久試験ではありません。観測RSS増分最大はnative 1.46MiB、
Higuma ASGI 2.34MiB、Higuma WSGI 0.79MiB、Flask 0.27MiB、Starlette 0.63MiBでした。
allocatorの再利用・事前workload・samplingの影響があり、uploadの厳密な割当量ではありません。

### I/O待機と同時接続数

同じ20ms模擬I/Oで同時接続16/64/128を別途3round測定しました。
[I/O生データ](assets/benchmarks/io-scaling-windows-2026-10-27.json)も公開しています。

| 方式 | c=16 (req/s) | c=64 | c=128 |
| --- | ---: | ---: | ---: |
| Higuma native | 511 | 1,668 | 2,861 |
| Higuma / Uvicorn | 415 | 1,177 | 1,151 |
| Higuma / Waitress | 474 | 509 | 474 |
| Flask / Waitress | 748 | 754 | 740 |
| Starlette / Uvicorn | 539 | 1,663 | 2,873 |

この測定ではnativeとStarletteはc=128で近い値でした。Higuma ASGIは調整worker上限64の
影響も受け、接続数を128へ増やしても伸びませんでした。WSGIの16thread構成は待機で頭打ちに
なります。Flaskの待機時間の違いは後述します。これは実DB接続数を増やしてよいという根拠ではありません。
main測定165ケースとI/O測定45ケースの全roundで、測定・warmupの通信エラーと不正応答は0でした。

再実行する場合は、次を通常測定とは重ねずに実行します。

```bash
python -m bench.suite --output target/io-scaling-new.json --rounds 3 --duration 1.5 --warmup 0.3 --concurrency 16 64 128 --cases io --upload-requests 0
```

## 解釈上の制約

- 同一PCがserverと負荷生成を担当するため、client側のCPU/GIL/socketが上限になることがあります。
- localhostだけの結果で、TLS・proxy・実DB・外部network・複数workerは評価していません。
- 20ms待機は模擬I/Oです。実際のDB性能や耐障害性の証明ではありません。
- RSS/CPUはserver process tree全体を対象にします。Windowsのvenv launcherと実際のserver子processを
  合算し、RSSは20ms間隔で観測します。割当量の厳密な帰属や瞬間peakを保証しません。
- 3roundは記述的な比較で、統計的有意差や全環境での優劣を主張しません。
- Higumaのuploadは受信完了までspoolします。WaitressもWSGI入力をbufferするため、
  Flaskの`request.stream`も必ずしもlive network readではありません。Starlette/Uvicornの
  live ASGI chunk読取とは処理開始時刻が異なります。
- ASGI対応によってHiguma内部がcoroutine-onlyになったわけではありません。
- Windowsのtimer解像度は結果に影響します。この環境の単独probeでは20ms指定の
  `asyncio.sleep`中央値が約31.1ms、`time.sleep`は約20.3msでした。Flaskの同期sleepと
  他方式のasync sleepを、完全に同じ実待機時間として速度比較しません。
  詳細は[PythonのWindows asyncio制約](https://docs.python.org/3/library/asyncio-platforms.html#windows)を参照してください。

入力bufferingの境界は[Waitressの設計](https://docs.pylonsproject.org/projects/waitress/en/stable/design.html)も参照してください。

目的は勝者を宣伝することではなく、どの条件で強み／弱みが出るかと、APIの使い勝手を
切り分けて判断することです。実運用では自分のアプリ・proxy・DBを含めて再測定してください。
