# ドキュメント INDEX

このリポジトリのドキュメントを **目的別** に引くためのナビゲーション。
「何をしたいか」から該当ページを見つけられるよう設計している。

______________________________________________________________________

## ヘッドライン

| 状況 | まず読むべき |
|---|---|
| **このシステムが何をやっているか知りたい** | [architecture.md](./architecture.md) → [business_context.md](./business_context.md) |
| **初めて触るので開発環境をセットアップしたい** | [../README.md](../README.md) §ローカル開発環境 |
| **どんなコマンドが使えるか分からない** | `make help`（リポジトリルートで実行） |
| **本番環境を構築したい（初回）** | [initial_setup.md](./initial_setup.md) |
| **アラートを追加・変更したい** | [alert_design.md](./alert_design.md) §3, §4 |
| **バッチが失敗した・通知が来ない** | [operations.md](./operations.md) §障害対応フロー |
| **テストを追加したい** | [testing.md](./testing.md) |
| **「なぜこの設計なのか」が知りたい** | [decisions.md](./decisions.md) |
| **何が自由で何が制約か知りたい** | [constraints_and_flexibility.md](./constraints_and_flexibility.md) |
| **用語の意味が分からない** | [glossary.md](./glossary.md) |

______________________________________________________________________

## 役割別の入り口

### 👨‍💻 新規参加エンジニア（オンボーディング）

1. [README.md](../README.md) — 全体像（5 分）
1. [architecture.md](./architecture.md) — システム構成と状態遷移（10 分）
1. [glossary.md](./glossary.md) — ドメイン用語（5 分）
1. [business_context.md](./business_context.md) — ビジネス背景（10 分）
1. [testing.md](./testing.md) — テストの書き方・実行方法（10 分）

合計 40 分で「読む側」になれる。

### 🛠 運用担当（オンコール）

1. [operations.md](./operations.md) — 障害対応フローと日常運用
1. [architecture.md](./architecture.md) §データフロー — 故障切り分けの地図として
1. [alert_design.md](./alert_design.md) §9 — Cloud Monitoring 設定

### 📐 設計レビュアー / アーキテクト

1. [requirements.md](./requirements.md) — 要件定義
1. [decisions.md](./decisions.md) — 設計上の選択肢と採用理由
1. [architecture.md](./architecture.md) — 現状アーキテクチャ
1. [data_source_investigation.md](./data_source_investigation.md) — Billing API/Export の調査結果

### 🏗 インフラ担当（Terraform レビュー）

1. [initial_setup.md](./initial_setup.md) — 手動セットアップ（Phase 1–3）
1. [../terraform/main.tf](../terraform/main.tf) — リソース定義
1. [decisions.md](./decisions.md) §8 — CI/CD 基盤の選定理由

______________________________________________________________________

## ドキュメント一覧（種類別）

### 入口・全体像

| ファイル | 内容 | サイズ目安 |
|---|---|---|
| [../README.md](../README.md) | リポジトリ正面玄関。5 分で全体像 | 短 |
| [INDEX.md](./INDEX.md) | このファイル。目的別ナビゲーション | 短 |
| [architecture.md](./architecture.md) | Mermaid 図で見るシステム構成・データフロー・状態遷移 | 中 |
| [glossary.md](./glossary.md) | 独自用語の定義集 | 中 |

### 要件・設計

| ファイル | 内容 |
|---|---|
| [requirements.md](./requirements.md) | 要件定義・テーブルスキーマ・処理フロー・テスト観点 |
| [business_context.md](./business_context.md) | 請求代行事業者視点のデータ活用目的 |
| [data_source_investigation.md](./data_source_investigation.md) | Billing API / Export の調査結果 |
| [alert_design.md](./alert_design.md) | アラートシステム設計・Function コード・Terraform 定義 |
| [merge_sql_prototype.md](./merge_sql_prototype.md) | MERGE SQL プロトタイプ |
| [decisions.md](./decisions.md) | 設計上の選択肢と採用・却下の記録 |
| [constraints_and_flexibility.md](./constraints_and_flexibility.md) | 自由度（変えやすい点）と制約（順序・GCP・設計）の整理 |

### 実装・運用

| ファイル | 内容 |
|---|---|
| [initial_setup.md](./initial_setup.md) | 初回セットアップ手順（Phase 1–4） |
| [todo.md](./todo.md) | 本番稼働までの手動作業 TODO リスト |
| [operations.md](./operations.md) | 障害対応フロー・日常運用・ロールバック |
| [testing.md](./testing.md) | テスト戦略・pytest 構造・テスト追加方法 |

______________________________________________________________________

## 「どこに何が書いてあるか」逆引き

| 知りたいこと | 該当ドキュメント |
|---|---|
| `billing_project_links` テーブルのスキーマ | [requirements.md](./requirements.md) §テーブル設計 |
| `status` の取りうる値と遷移条件 | [architecture.md](./architecture.md) §状態遷移図 |
| `billing_newly_started` フラグのリセットタイミング | [alert_design.md](./alert_design.md) §7、[decisions.md](./decisions.md) §1 の「注意」 |
| Billing Export のスキーマと `billing_account_id` の意味 | [data_source_investigation.md](./data_source_investigation.md) |
| なぜ単一コンテナ + `BATCH_TYPE` 環境変数か | [decisions.md](./decisions.md) §10 |
| Cloud Monitoring 通知チャンネルの手動設定 | [initial_setup.md](./initial_setup.md) §4-2 |
| Workload Identity Federation の設定 | [initial_setup.md](./initial_setup.md) §2-5 |
| Slack Bot Token の取得と Secret 登録 | [initial_setup.md](./initial_setup.md) §3-2, §3-3 |
| バッチ失敗時の手動再実行コマンド | [operations.md](./operations.md) §バッチ手動再実行 |
| アラート通知先（Slack チャンネル）の変更方法 | [alert_design.md](./alert_design.md) §3、[operations.md](./operations.md) §アラート変更 |
| `terraform.tfvars` の各変数の意味 | [../terraform/variables.tf](../terraform/variables.tf) |
| 月次バッチが前月の何日に実行されるか | [decisions.md](./decisions.md) §10、Cloud Scheduler 定義は [main.tf](../terraform/main.tf) |
