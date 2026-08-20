# Power Automate Connector — Connector Discovery

**Дата discovery:** 2026-08-20
**Решение Влада (2026-08-20):** Слой B (Dataverse Web API) как основа Яруса 1/2. Объём релиза — **Ярус 3** (полное покрытие + наши value-add функции, например bulk-операции). Слой C (недокументированный `api.flow.microsoft.com`) НЕ используется в этом заходе — осознанный выбор стабильности/официальной поддержки над покрытием "My Flows". Следствие: коннектор управляет **Solution-aware flows** (flows, добавленные в Dataverse Solution) через официальный, документированный, стабильный API. Личные недобавленные в Solution "My Flows" вне охвата этого коннектора — это ограничение самого Microsoft API, не наше; должно быть явно и честно написано в описании приложения в Marketplace, чтобы пользователь не был удивлён при подключении.

**Дополнительное архитектурное решение по auth (принято 2026-08-20 в процессе Фазы 2, по прямой аналогии с уже устоявшимся портфельным паттерном — не отдельный вопрос Владу, т.к. Microsoft сама рекомендует именно это для server-to-server сценариев):**

BYOK через Azure AD App Registration + Application User в Dataverse (client credentials flow), НЕ через платформенный общий `ext.oauth("microsoft", ...)`. Причины:
1. SDK уже имеет `"microsoft"` в своём статичном `_AUTHORIZE` словаре (login.microsoftonline.com/common/oauth2/v2.0/authorize) — но этот механизм строит OAuth `scope` из **фиксированного списка, заданного при декларации** `ext.oauth(provider, scopes=[...])`. Dataverse требует scope вида `https://org12345.crm.dynamics.com/.default` — привязанный к URL **конкретного окружения пользователя**, неизвестного заранее. Это тот же "курица и яйцо", что уже решило n8n Connector явным полем `base_url` вместо auto-discovery (см. n8n PREPARATION.md §4).\n2. Раньше эту проблему решал Global Discovery Service (перечислял все org пользователя после общего логина) — но GDS **официально в процессе retirement с 2026-06-19** (Microsoft MC1253577), заменён на новый Power Platform \"List Environments\" API. Строить архитектуру на умирающем сервисе было бы ошибкой.\n3. Официальная документация Microsoft (learn.microsoft.com/power-apps/developer/data-platform/authenticate-oauth, walkthrough-register-app-azure-active-directory) сама рекомендует Application User + security role в Dataverse для server-to-server интеграций — то есть решение не изобретено нами, а прямо указано производителем API.\n\n**Итоговая connect-модель (аналог n8n's base_url + api_key, только с 4 полями вместо 2):**\n- `tenant_id` — Azure AD tenant ID пользователя.\n- `client_id` / `client_secret` — из его собственного App Registration (пользователь создаёт сам, разово, у себя в Azure Portal — так же, как он создаёт свой n8n API-key или Make.com токен).\n- `environment_url` — URL конкретного Dataverse-окружения (например `https://org12345.crm.dynamics.com`) — вводится пользователем явно, аналогично n8n's `base_url`, т.к. авто-discovery через GDS устарел, а новый List Environments API даёт список окружений уже ПОСЛЕ входа — избыточная сложность для Яруса 1, когда пользователь и так знает URL своего окружения.
**Источники:** learn.microsoft.com/power-automate/manage-flows-with-code, learn.microsoft.com/connectors/flowmanagement, learn.microsoft.com/rest/api/power-platform/powerautomate/flow-runs/list-flow-runs, learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/workflow, learn.microsoft.com/power-platform/admin/wp-data-loss-prevention, ashiqf.com (Power Automate REST API notes, reverse-engineered), community.dynamics.com (Power Automate Management connector deep dive), stackoverflow.com/questions/76980177, github.com/api-evangelist/microsoft-power-automate (community OpenAPI reconstruction).

## 1. Целевой сервис и источники

Microsoft Power Automate — часть Power Platform (Microsoft 365/Dynamics 365 экосистема). В отличие от всех остальных 22 коннекторов в портфеле, у Power Automate **нет единого, полностью официально задокументированного публичного REST API** для управления пользовательскими flow. Вместо этого существуют три РАЗНЫЕ поверхности с разной степенью официальности и покрытия — это ключевая находка, определяющая архитектуру:

| Слой | Официальность | Что покрывает | Ограничение |
|---|---|---|---|
| **A. Power Platform REST API** (`/rest/api/power-platform/powerautomate/flow-runs/*`) | Официально задокументировано Microsoft, OAuth2 | List/Get/Cancel/Resubmit flow **runs** | Требует Dataverse-базу в окружении — на дефолтном/trial окружении без Dataverse вернёт 404 |
| **B. Dataverse Web API** (`/api/data/v9.2/workflows`, entity `workflow`, category=5 "Modern Flow") | Официально задокументировано Microsoft | Полный CRUD над flow как Dataverse-записью (create/get/update/delete, turn on/off через `statecode`) | **Работает только для "Solution"-flows. Личные "My Flows" через код НЕ управляются — прямая цитата документации Microsoft: "managing flows under My Flows aren't supported with code"** |
| **C. `api.flow.microsoft.com` / Microsoft.ProcessSimple resource provider** | **НЕ задокументировано официально Microsoft** — восстановлено сообществом по аналогии с Azure Logic Apps REST API | Самое широкое покрытие: Create/Get/Update/Delete Flow, Turn On/Off, List **My Flows**, List Flows as Admin, Cancel/Resubmit Flow Run, List Callback URL, Modify Flow Owners | Официально это подаётся Microsoft только как встроенный action ВНУТРИ другого flow (коннектор `Power Automate Management`), не как внешний API для сторонних приложений. Работает при вызове напрямую (подтверждено несколькими независимыми community-источниками), но без гарантий обратной совместимости от Microsoft |
| **D. HTTP-триггер конкретного flow** | Официально задокументировано (стандартный HTTP Request trigger внутри Power Automate) | Egress: дёрнуть ОДИН конкретный, заранее созданный flow по его собственному webhook URL | Не даёт списка/управления flow — только вызов того, что уже настроено пользователем вручную |
| **E. Power Platform Admin API** (`environmentmanagement`, DLP policies) | Официально задокументировано | Environments, Data Loss Prevention policies — административный уровень тенанта | Требует Global Admin/Power Platform Admin роль и часто app-only (Service Principal) auth, не обычный delegated user OAuth |

**Аутентификация (все слои):** Microsoft Entra ID (Azure AD) OAuth 2.0. Тот же механизм, что уже реализован в приложении `mail` для Outlook/Microsoft 365 — паттерн OAuth-подключения Microsoft-аккаунта можно переиспользовать, а не строить с нуля.

## 2. Карта возможностей (направление)

| Возможность | Ingress/Egress/Both | Слой |
|---|---|---|
| List My Flows | Ingress | C (недокументировано) |
| List Flows as Admin | Ingress | C (недокументировано) |
| Get Flow | Ingress | B (Solution flows) / C (My Flows) |
| Create Flow | Egress | B (Solution flows) / C (My Flows) |
| Update Flow | Egress | B (Solution flows) / C (My Flows) |
| Delete Flow | Egress | B (Solution flows) / C (My Flows) |
| Turn On/Off Flow | Egress | B (`statecode`, Solution flows) / C (My Flows) |
| List Flow Runs | Ingress | A (официально, требует Dataverse) / C |
| Get Flow Run | Ingress | A / C |
| Cancel Flow Run | Egress | A (официально) / C |
| Resubmit/Retry Flow Run | Egress | A (официально) / C |
| List Callback URL | Ingress | C (недокументировано) |
| Modify Flow Owners | Egress | C (недокументировано) |
| Invoke specific flow via HTTP trigger | Egress | D (официально, но только для заранее настроенного flow) |
| List Environments | Ingress | E (admin) |
| List/Set DLP policies | Both | E (admin, требует admin-роль) |

## 3. Ярус 1 — Ключевые функции (P0-кандидаты)

Независимо от выбранного архитектурного слоя (см. Вопрос 1 ниже), P0 — это:
- `connect_microsoft` (переиспользуя OAuth-паттерн из `mail`)
- `list_flows` (My Flows + Solution flows, если оба слоя реализуются)
- `get_flow`
- `enable_flow` / `disable_flow` (turn on/off — самая частая реальная боль пользователя: "flow сломался, срочно выключить")
- `list_flow_runs`
- `get_flow_run`
- `cancel_flow_run`
- `trigger_flow` (через HTTP-триггер конкретного flow, слой D — единственный способ РЕАЛЬНО "запустить" flow программно, а не просто посмотреть на него)

## 4. Ярус 2 — Полное покрытие

| Возможность | Статус | Причина/триггер |
|---|---|---|
| Create Flow | included | Полезно для программного создания простых flow из шаблона |
| Update Flow | included | Правка существующего flow |
| Delete Flow | included | Стандартная деструктивная операция портфеля |
| Resubmit Flow Run | included | Прямой аналог `retry` у n8n/Make |
| List Callback URL | included | Нужно для `trigger_flow`, чтобы получить URL программно, а не просить пользователя копировать вручную |
| Modify Flow Owners | deferred | Редкий сценарий (передача владения), не блокирует P0 |
| List Flows as Admin | deferred | Требует admin-права тенанта — не у каждого пользователя Imperal они есть; отдельный флаг доступности |
| List Environments (слой E) | deferred | Административный уровень, отдельный от повседневной работы с flow |
| List/Set DLP policies (слой E) | deferred | Требует Global Admin — специфичный enterprise-сценарий, не P0 массового пользователя |

## 5. Ярус 3 — Функции на нашей стороне (value-add)

- **`bulk_set_flow_state`** — включить/выключить сразу несколько flow по фильтру (ни один официальный слой не даёт bulk-операцию нативно — community-примеры делают это циклом; можно обернуть на нашей стороне, как `bulk_run_scenarios` в Make.com Connector).
- **Health-дайджест по flow** — агрегированный отчёт "какие flow сейчас failed/disabled" по всем окружениям сразу (комбинация List My Flows + List Flow Runs, которых по отдельности сервис не связывает в одном ответе).
- **Fallback-обёртка `trigger_flow`**: если у flow нет HTTP-триггера — понятная ошибка "добавь Request-триггер в этот flow", а не тихий сбой (прямая параллель тому, как n8n Connector обрабатывает 404 на run-endpoint).

## 6. Решение по объёму этого захода

**Не подтверждено заранее** — объём для конкретно этого коннектора не был заявлен явно в исходном сообщении (в отличие от того, как это иногда бывает по другим темам). По стандарту это единственный случай, когда нужно явно спросить — см. вопросы ниже, до перехода к Фазе 3.
