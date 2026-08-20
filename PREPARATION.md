# Power Automate Connector — Preparation

**Статус:** Фаза 1 (Discovery + архитектурные решения) завершена, решения
подтверждены Владом 2026-08-20. Готово к Фазе 2 (дизайн панелей) /
Фазе 3 (реализация).
**Владелец продукта:** vlad@bluebeeweb.com
**Дата подготовки:** 2026-08-20, v0.1
**Vikunja task:** #2155 (BBW Imperal Apps), [App Development], priority High.

**Почему сейчас:** Microsoft Power Automate — самый доминирующий игрок в
категории no-code automation (по нашей composite-оценке ~27% рынка,
существенно выше Zapier ~21% и всех остальных). В портфеле Imperal уже
есть коннекторы к Make.com и n8n (тот же класс интеграций, тот же
проверенный BYOK-паттерн). Power Automate — естественное и самое ценное
расширение этой категории, выбрано первым из шести (см. задачи
#2140–#2155) как наиболее востребованное.

---

## 1. Паспорт приложения

**Название в Marketplace (display_name): «Power Automate»** — по той же
логике сокращения, что и Make.com/n8n (см. их PREPARATION.md раздел 1).
Внутренний app_id/папка: `power-automate-connector`.

**Power Automate Connector** — коннектор к Microsoft Power Automate через
Dataverse Web API. Даёт Webbee возможность от имени пользователя читать
и управлять его cloud flows (list/get/create/update/delete,
enable/disable), видеть их runs (list/get/cancel/resubmit — где
поддерживается платформой), и выполнять bulk-операции (наш value-add,
не входит в нативный API Microsoft). Как и Make.com/n8n Connector — это
BYOK: пользователь подключает свою собственную Power Platform среду
(Dataverse environment) через собственную Azure AD App Registration;
Imperal ничего не хостит и не проксирует помимо самого запроса.

---

## 2. Ключевые факты о Power Automate API (найдено в Discovery,
см. `CONNECTOR_DISCOVERY.md` для полного разбора трёх слоёв)

### 2.1 Три поверхности API — и почему выбран именно Слой B

Power Automate — единственный коннектор в портфеле, у которого нет
одного официального, полного публичного REST API. Есть три слоя:

- **Слой A** — Power Platform REST API (`/rest/api/power-platform/
  powerautomate/flow-runs/*`) — официальный, но только про **runs**
  (list/get/cancel/resubmit), не про сами flow, и требует Dataverse в
  окружении.
- **Слой B — ВЫБРАН.** Dataverse Web API (`/api/data/v9.2/workflows`,
  entity `workflow`) — официальный, документированный, полный CRUD
  над flow как Dataverse-записью. Ограничение: работает **только для
  Solution-aware flows** — личные недобавленные в Solution "My Flows"
  через код не управляются (прямая цитата документации Microsoft:
  "managing flows under My Flows aren't supported with code",
  learn.microsoft.com/power-automate/manage-flows-with-code).
- **Слой C** — недокументированный `api.flow.microsoft.com`
  (`Microsoft.ProcessSimple`) — шире (управляет и "My Flows"), но
  Microsoft НЕ документирует его официально; работает по данным
  community-реконструкции (ashiqf.com, api-evangelist). Решение
  Влада: НЕ использовать в этом заходе — стабильность и официальная
  поддержка важнее максимального покрытия.

**Следствие для пользователя:** коннектор управляет flow, которые
находятся в Dataverse Solution (стандартная практика для
организационного/командного использования Power Automate — Managed/
Unmanaged solutions). Личные "My Flows" пользователя, не добавленные в
Solution, не видны и не управляются этим коннектором — это ограничение
самого Microsoft API, должно быть явно написано в описании приложения
в Marketplace.

### 2.2 Workflow entity (Dataverse Web API) — реальные поля

Подтверждено против learn.microsoft.com/power-apps/developer/
data-platform/webapi/reference/workflow и официального образца
Microsoft (`SetStateWorkflow.cs`, github.com/microsoft/PowerApps-Samples):

- Entity set: `[organization URI]/api/data/v9.2/workflows`
- Primary key: `workflowid` (guid), primary name: `name`
- Operations supported: **POST, GET, PATCH, DELETE**
- `category` (Choice): `0`=Classic Dataverse workflow, `1`=dialog,
  `2`=business rule, `3`=classic action, `4`=business process flow,
  **`5`=Modern Flow (cloud flow — automated/instant/scheduled)**,
  `6`=desktop flow. Наш коннектор фильтрует/создаёт только `category=5`.
- `clientdata` (String) — JSON-кодированное определение flow +
  connectionReferences (сам flow definition).
- `statecode` (State, enum `WorkflowState`): `0`=Draft, `1`=Activated,
  `2`=Suspended. Это то же самое, что "включён/выключен" в UI.
- `statuscode` (Status, enum `workflow_statuscode`) — вторичный код,
  должен соответствовать `statecode` (Draft→Draft, Activated→
  Activated) — операция `SetState` требует передавать оба значения
  одновременно, не одно.
- `uniquename`, `solutionid`, `createdby`, `createdon`, `description`,
  `ismanaged` — метаданные.
- Bound actions на сущности (вызываются как `/workflows({id})/
  Microsoft.Dynamics.CRM.<Action>`): `ExecuteWorkflow` (запуск on-demand
  flow), `CancelAllCloudFlowRuns`, `RetrieveUnpublished`,
  `ListConnectionReferences`, `CreateWorkflowFromTemplate`.

### 2.3 Auth — BYOK через Azure AD App Registration (архитектурное
решение, зафиксировано в CONNECTOR_DISCOVERY.md)

НЕ через платформенный общий `ext.oauth("microsoft", ...)` (SDK уже
имеет "microsoft" в статичном `_AUTHORIZE`-словаре, но тот механизм
строит фиксированный `scope` при декларации — а Dataverse требует
scope вида `https://org12345.crm.dynamics.com/.default`, привязанный к
URL конкретного окружения пользователя, неизвестного заранее — тот же
"курица и яйцо", что уже решило n8n Connector явным полем `base_url`).

Вместо этого — **client credentials flow**: пользователь один раз
регистрирует Azure AD App Registration в своём тенанте (Microsoft сама
рекомендует это для server-to-server сценариев), создаёт Application
User с нужной security role в своём Dataverse environment, и передаёт
нам:

1. `tenant_id` — Azure AD tenant ID
2. `client_id` — Application (client) ID зарегистрированного app
3. `client_secret` — client secret
4. `environment_url` — URL конкретного Dataverse environment
   (например `https://org12345.crm.dynamics.com`), явно введённый
   пользователем — ПО ТОЙ ЖЕ причине, что и `base_url` у n8n: нет
   надёжного auto-discovery (Global Discovery Service, который решал
   эту проблему раньше, **в процессе retirement с 2026-06-19**,
   Microsoft MC1253577 — строить архитектуру на устаревающем сервисе
   было бы ошибкой).

Токен получается через `https://login.microsoftonline.com/{tenant_id}/
oauth2/v2.0/token` с `grant_type=client_credentials` и
`scope={environment_url}/.default` — стандартный OAuth2 client
credentials flow, реализуется вручную в `power_automate_client.py` (по
аналогии с тем, как `n8n_client.py`/`make_client.py` строят собственный
HTTP-слой без использования платформенного `ext.oauth`).

### 2.4 Flow Runs (Слой A, компаньон к Слою B)

Раз Dataverse в окружении уже обязателен для Слоя B, официальный
Power Platform REST API для runs естественно доступен как компаньон:
`GET/POST /rest/api/power-platform/powerautomate/environments/
{environment}/flow-runs` (list/cancel/resubmit) — тот же токен
(client credentials), другой resource/scope
(`https://api.powerplatform.com/.default`).

---

## 3. Решённые архитектурные вопросы

| # | Вопрос | Решение | Обоснование |
|---|---|---|---|
| 1 | BYOK или центральный брокер? | **BYOK**, как Make.com/n8n | Пользователь управляет своим собственным Power Platform tenant; Imperal не хостит и не является посредником организационных данных Dataverse. |
| 2 | Какой слой API? | **Слой B (Dataverse Web API)** + Слой A (flow runs) как компаньон | Официально документировано, стабильно. Слой C (недокументированный) сознательно отклонён — решение Влада 2026-08-20. |
| 3 | Auth механизм? | **Azure AD App Registration, client credentials flow**, НЕ платформенный `ext.oauth` | Dataverse требует scope, привязанный к конкретному environment URL, неизвестному заранее — платформенный OAuth строит фиксированный scope при декларации. |
| 4 | Сколько секретов? | **Четыре**: `tenant_id`, `client_id`, `client_secret`, `environment_url` | Все четыре обязательны для получения токена и адресации конкретного Dataverse environment. |
| 5 | Объём релиза? | **Ярус 3** — полное покрытие Слоя B/A + value-add bulk-операции | Решение Влада 2026-08-20 (`1 - Слой B, 2 - Ярус 3`). |
| 6 | "My Flows" (личные, вне Solution)? | **Вне охвата**, явно написано в описании приложения | Ограничение самого Microsoft API (Слой B), не наше решение — задокументировано Microsoft буквально. |

---

## 4. Функциональный охват (Ярус 3)

### Ярус 1 (P0 — базовый CRUD, обязателен)
- `connect_power_automate` (tenant_id, client_id, client_secret,
  environment_url) — проверка + сохранение через `ctx.secrets`.
- `disconnect_power_automate`
- `list_flows` (category=5, фильтр по statecode)
- `get_flow`
- `create_flow` (clientdata JSON + name + category=5)
- `update_flow`
- `delete_flow`
- `enable_flow` / `disable_flow` (SetState statecode/statuscode пара)
- `run_flow` (bound action `ExecuteWorkflow`, где поддерживается —
  честная обработка ошибки, если flow не on-demand-triggered, по
  аналогии с тем, как n8n Connector различает "нет эндпоинта" от
  "нет доступа")

### Ярус 2 (полное покрытие естественных возможностей API)
- `list_flow_runs` / `get_flow_run` (Слой A)
- `cancel_flow_run` / `resubmit_flow_run` (Слой A)
- `list_connection_references` (bound action `ListConnectionReferences`)
- `cancel_all_cloud_flow_runs` (bound action, уже встроенная bulk-
  операция на стороне самого Dataverse — не эмулируем циклом)
- `create_flow_from_template` (bound action `CreateWorkflowFromTemplate`)
- `retrieve_unpublished` (черновая незапущенная версия flow)

### Ярус 3 (наши value-add функции — bulk-операции)
- `bulk_enable_flows` / `bulk_disable_flows` (explicit id list, 1-100,
  по паттерну `apply_bulk_*` из WordPress Hub/Make.com Connector —
  предпросмотр + explicit re-check state token перед записью)
- `bulk_delete_flows` (explicit id list, деструктивная — подтверждение)
- `bulk_run_flows` (explicit id list, запуск нескольких on-demand flow
  за один вызов, по аналогии с `bulk_run_scenarios` в Make.com Connector)

---

## 5. Открытые вопросы для Влада

Нет открытых вопросов — оба обязательных решения (слой API, объём
релиза) подтверждены 2026-08-20. Auth-архитектура решена по прямой
аналогии с устоявшимся портфельным паттерном (не отдельный вопрос).

---

## 6. Журнал проверки дублей

`search_marketplace` по «Power Automate» и общим терминам workflow/
automation (см. также проверку для n8n Connector) — дублей не найдено
в существующем портфеле Imperal на момент 2026-08-20.
