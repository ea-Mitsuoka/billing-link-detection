# アーキテクチャ

このシステム全体を **図で理解する** ためのページ。テキスト詳細は [requirements.md](./requirements.md) と [alert_design.md](./alert_design.md) を参照。

______________________________________________________________________

## 1. システム全体構成

```mermaid
flowchart TB
    subgraph external["外部サービス"]
        BillAPI["Cloud Billing API<br/>（リンク情報の真実）"]
        BillExport["Cloud Billing Export<br/>（請求金額の真実）"]
        Slack["Slack<br/>Bot API"]
    end

    subgraph analysis["分析システムプロジェクト"]
        Sched1["Cloud Scheduler<br/>日次 02:00 JST"]
        Sched2["Cloud Scheduler<br/>月次 5日 03:00 JST"]
        SchedAlert["Cloud Scheduler<br/>（アラート3種）"]

        JobDaily["Cloud Run Job<br/>billing-collector<br/>BATCH_TYPE=daily"]
        JobMonthly["Cloud Run Job<br/>billing-cost-updater<br/>BATCH_TYPE=monthly"]
        Func["Cloud Functions Gen2<br/>alert-handler"]

        BQ[("BigQuery<br/>billing_data.billing_project_links")]

        Secret["Secret Manager<br/>slack-bot-token"]
        Mon["Cloud Monitoring<br/>ログベース → Slack"]
    end

    subgraph export_project["Billing Export 専用プロジェクト<br/>（dragon.jp 用）"]
        BQExport[("BigQuery<br/>gcp_billing_export_v1_XXX")]
    end

    Sched1 --> JobDaily
    Sched2 --> JobMonthly
    SchedAlert --> Func

    JobDaily -->|"list_billing_accounts<br/>list_project_billing_info"| BillAPI
    JobDaily -->|"MERGE / UNLINKED 検知"| BQ
    JobDaily -.->|"ever_billed 更新"| BQExport

    JobMonthly -->|"前月コスト集計"| BQExport
    JobMonthly -->|"prev_month_cost MERGE"| BQ

    BillExport -->|"Cloud 公式の<br/>定期エクスポート"| BQExport

    Func -->|"クエリ"| BQ
    Func -->|"Bearer Token 読込"| Secret
    Func -->|"chat.postMessage"| Slack

    JobDaily -->|"構造化ログ"| Mon
    JobMonthly -->|"構造化ログ"| Mon
    Func -->|"構造化ログ"| Mon
    Mon -->|"システムエラー通知"| Slack
```

**読み方のポイント**

- **データの真実** は GCP の Billing API（リンク情報）と Billing Export（請求金額）の 2 つに分かれている。Billing API は課金金額を返せないため、コスト集計には Billing Export の BigQuery 出力が必須（詳細は [decisions.md §12](./decisions.md)）
- 日次バッチ `billing-collector` がこの 2 つを統合して `billing_project_links` テーブルを最新化する
- 月次バッチ `billing-cost-updater` は前月の請求金額だけを更新する（リンク情報には触らない）
- アラートは Cloud Functions が `billing_project_links` をクエリして Slack に投げるだけのシンプルな設計
- システムエラー（バッチ失敗・Function 失敗）は Cloud Monitoring が拾って別チャンネルへ

______________________________________________________________________

## 2. データフロー（日次バッチ）

```mermaid
sequenceDiagram
    autonumber
    participant S as Cloud Scheduler
    participant J as Cloud Run Job<br/>billing-collector
    participant API as Cloud Billing API
    participant BQ as BigQuery<br/>billing_project_links
    participant Tmp as BigQuery<br/>_tmp_billing_links
    participant Exp as Billing Export<br/>gcp_billing_export_v1_XXX

    S->>J: 02:00 JST 起動
    J->>BQ: Step 1: billing_newly_started=FALSE<br/>（昨日 TRUE だったレコードをリセット）
    J->>API: Step 2: list_billing_accounts<br/>+ list_project_billing_info
    API-->>J: 全サブアカウント × 全プロジェクト
    J->>Tmp: Step 3: _tmp_billing_links に書き込み<br/>（WRITE_TRUNCATE）
    Note over J,BQ: BEGIN TRANSACTION
    J->>BQ: Step 4: MERGE<br/>（INSERT / UPDATE / 再リンク検知）
    J->>BQ: Step 5: UPDATE status='UNLINKED'<br/>WHERE last_fetched_at < @batch_run_at
    Note over J,BQ: COMMIT TRANSACTION
    J->>Exp: Step 6: SCAN<br/>SUM(cost) > 0 のプロジェクトを抽出
    J->>BQ: Step 7: ever_billed=TRUE<br/>billing_newly_started=TRUE
```

**ポイント**

- Step 4–5 は **トランザクション** にまとめて原子性を担保（途中失敗で UNLINKED 化が中途半端に走るのを防ぐ）
- Step 5 の `last_fetched_at < @batch_run_at` で「今回 API に出てこなかった既存レコード」を検出する
- Step 6–7 は Billing Export が未設定でもスキップ可能（warning ログ）

______________________________________________________________________

## 3. データフロー（月次バッチ）

```mermaid
sequenceDiagram
    autonumber
    participant S as Cloud Scheduler<br/>毎月5日 03:00
    participant J as Cloud Run Job<br/>billing-cost-updater
    participant Exp as Billing Export
    participant Tmp as _tmp_monthly_cost
    participant BQ as billing_project_links

    S->>J: 起動（BATCH_TYPE=monthly）
    J->>J: _prev_month_yyyymm() で<br/>例: 2026年5月実行 → "202604"
    J->>Exp: WHERE invoice.month = '202604'<br/>GROUP BY project_id, billing_account_id<br/>SUM(cost)
    Exp-->>J: 前月コスト
    J->>Tmp: 集計結果を一時テーブルへ
    J->>BQ: MERGE with LEFT JOIN<br/>（出現しなかったレコードも<br/>prev_month_cost=0 で更新）
```

**なぜ LEFT JOIN か**: Billing Export に「コストが発生したプロジェクトしか出てこない」性質があるため、`_tmp_monthly_cost` を USING に直接置くと該当しないプロジェクトの `prev_month_cost` が更新されず、前月の値が居残る。`billing_project_links LEFT JOIN _tmp_monthly_cost` で **全件 0 補完** している。

______________________________________________________________________

## 4. アラート起動フロー

```mermaid
sequenceDiagram
    participant S as Cloud Scheduler
    participant F as Cloud Functions<br/>alert-handler
    participant BQ as BigQuery
    participant SM as Secret Manager
    participant Slack as Slack chat.postMessage

    S->>F: HTTP POST body:<br/>{ query, channel, message }
    F->>F: query 内の {project}/{dataset}<br/>を env から置換
    F->>BQ: クエリ実行<br/>（maximum_bytes_billed=10GB）
    BQ-->>F: 結果行
    alt 結果が空
        F-->>S: 200 "no results"
    else 結果あり
        F->>SM: SLACK_BOT_TOKEN（環境変数経由）
        F->>Slack: chat.postMessage<br/>（最大50行、超過分は省略）
        Slack-->>F: { ok: true }
        F-->>S: 200 "ok"
    end
```

**設計のキモ**

- アラート 1 件 = `alerts.yaml` の 1 エントリ = Cloud Scheduler 1 ジョブ（Terraform `for_each`）
- 通知を止めたい → `gcloud scheduler jobs pause` だけで完結。コード変更不要
- SQL は YAML 内にフルで書く → BigQuery コンソールに貼り付けて即動作確認できる

詳細は [alert_design.md](./alert_design.md)。

______________________________________________________________________

## 5. `billing_project_links.status` の状態遷移図

```mermaid
flowchart LR
    START(["初回発見\n→ INSERT"]) --> ACTIVE

    subgraph inAPI["API に出現している（バッチ毎に MERGE で再評価）"]
        direction TB
        ACTIVE["ACTIVE\n正常リンク中"]
        BD["BILLING_DISABLED\n課金停止\nbilling_enabled = FALSE"]
        SC["SUB_CLOSED\nアカウント閉鎖\nsub_account_open = FALSE"]

        ACTIVE -->|"billing_enabled = FALSE"| BD
        BD -->|"billing_enabled = TRUE に復帰"| ACTIVE
        ACTIVE -->|"sub_account_open = FALSE"| SC
        BD -->|"sub_account_open = FALSE"| SC
        SC -->|"再オープン & 課金有効"| ACTIVE
    end

    UL["UNLINKED\n今回バッチで\nAPI に出現せず"]

    ACTIVE & BD & SC -->|"API 不出現"| UL
    UL -->|"API 再出現 → MERGE 再評価"| ACTIVE
    UL -.->|"再出現 &\nbilling_enabled=FALSE"| BD
    UL -.->|"再出現 &\nsub_account_open=FALSE"| SC
```

**遷移の判定ロジック**（MERGE 内 CASE 式）

```
status = CASE
  WHEN billing_enabled  = FALSE THEN 'BILLING_DISABLED'
  WHEN sub_account_open = FALSE THEN 'SUB_CLOSED'
  ELSE 'ACTIVE'
END
```

**UNLINKED の判定**は MERGE の後に別 UPDATE で実施：

```
WHERE status != 'UNLINKED'
  AND last_fetched_at < @batch_run_at
```

つまり「今回バッチで `last_fetched_at` が更新されなかった既存レコード = API に出てこなかった」を UNLINKED とする。

______________________________________________________________________

## 6. テーブル間の関係

```mermaid
erDiagram
    billing_project_links {
        STRING parent_account_id PK "親請求先アカウントID"
        STRING sub_account_id PK "サブアカウントID"
        STRING sub_account_name "サブアカウント表示名"
        STRING project_id PK "プロジェクトID"
        BOOL billing_enabled "課金有効状態"
        BOOL sub_account_open "サブアカウント開設状態"
        STRING status "ACTIVE/UNLINKED/BILLING_DISABLED/SUB_CLOSED"
        TIMESTAMP linked_at
        TIMESTAMP unlinked_at "NULL=現在リンク中"
        TIMESTAMP relinked_at
        INT64 link_count
        TIMESTAMP last_fetched_at "APIに出現しなくなった検知に使用"
        TIMESTAMP updated_at "内容が変化したときのみ更新"
        FLOAT64 prev_month_cost "月次バッチで更新"
        STRING cost_currency "prev_month_cost の通貨コード"
        BOOL ever_billed "Billing Export から判定"
        STRING first_billed_month "YYYY-MM"
        BOOL billing_newly_started "翌日のバッチでリセット"
    }

    _tmp_billing_links {
        STRING parent_account_id
        STRING sub_account_id
        STRING sub_account_name
        STRING project_id
        BOOL billing_enabled
        BOOL sub_account_open
        TEXT NOTE "日次バッチ Step3 で<br/>WRITE_TRUNCATE"
    }

    _tmp_monthly_cost {
        STRING project_id
        STRING sub_account_id
        FLOAT64 prev_month_cost
        STRING cost_currency
        TEXT NOTE "月次バッチで<br/>WRITE_TRUNCATE"
    }

    gcp_billing_export_v1_XXX {
        STRING billing_account_id "= sub_account_id"
        RECORD project "project.id を使う"
        RECORD invoice "invoice.month=YYYYMM"
        FLOAT64 cost
        STRING currency
        TEXT NOTE "GCP 公式の<br/>定期エクスポート"
    }

    billing_project_links ||--o{ _tmp_billing_links: "Step4 MERGE で更新元"
    billing_project_links ||--o{ _tmp_monthly_cost: "月次 MERGE で更新元"
    _tmp_monthly_cost }o--|| gcp_billing_export_v1_XXX: "前月分集計"
```

**重要な事実**: GCP 公式ドキュメント([参照](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage))により、Billing Export の `billing_account_id` は **販売パートナーの場合「サブアカウント ID」が入る**（親アカウント ID ではない）。これにより `_tmp_monthly_cost.sub_account_id` と `billing_project_links.sub_account_id` で正しく JOIN できる。

______________________________________________________________________

## 7. プロジェクト分離（2 プロジェクト構成）

```mermaid
flowchart LR
    subgraph billing_acct_dragon["dragon.jp 親請求先アカウント"]
        direction TB
        sub1["サブアカウント1"]
        sub2["サブアカウント2"]
        subN["サブアカウントN"]
    end

    subgraph proj_export["Billing Export 専用プロジェクト<br/>（dragon.jp 直接リンク）"]
        ds_export[("BigQuery<br/>billing_data.gcp_billing_export_v1_XXX")]
    end

    subgraph proj_analysis["分析システムプロジェクト"]
        ds_analysis[("BigQuery<br/>billing_data.billing_project_links")]
        sa["sa-billing-collector"]
    end

    billing_acct_dragon -->|"標準コスト<br/>エクスポート"| ds_export
    sa -.->|"クロスプロジェクト<br/>bigquery.dataViewer"| ds_export
    sa -->|"読み書き<br/>bigquery.dataEditor"| ds_analysis
```

**なぜ 2 プロジェクトに分けるか**

- Billing Export の設定 UI は「親請求先アカウント直下のプロジェクト」しか選べないため、別請求先アカウントにリンク済みの分析システムプロジェクトを Export 先にできない
- 分析システムと Export 先を一緒のプロジェクトにすると、Export 先プロジェクトを差し替えるたびに分析システムも巻き込まれる
- セキュリティ境界の分離: Billing Export は親請求先アカウント管理者の所有物。分析システム側で誤更新しないよう IAM を分けたい

詳細は [decisions.md](./decisions.md) §11（プロジェクト分離）を参照。

______________________________________________________________________

## 8. CI/CD パイプライン

```mermaid
flowchart LR
    Dev[開発者] -->|PR| GH[GitHub]
    GH -->|trigger| LT[lint-and-test]

    LT --> TPlan{PR か<br/>main push か}
    TPlan -->|PR| Plan[terraform plan<br/>結果を PR に表示]
    TPlan -->|main push| Build[Docker build & push]

    Build --> Apply[terraform apply<br/>TF_VAR_batch_image を上書き]
    Apply -->|WIF| GCP[(GCP リソース更新)]

    subgraph LT
        T1[pytest] --> T2[terraform fmt -check]
        T2 --> T3[terraform validate]
    end
```

詳細は [.github/workflows/deploy.yml](../.github/workflows/deploy.yml) と [decisions.md](./decisions.md) §8。
