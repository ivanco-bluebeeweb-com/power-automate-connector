# Scenario Tests (PST) — Power Automate Connector

Метод: `Docs/session-notes/SCENARIO_TESTING_STANDARD.md`. Этот файл — обязательный
журнал прогонов Plausible Scenario Testing для этого приложения, дополняющий
(не заменяющий) пост-аудит (Слой 1): пост-аудит сверяет статическую
согласованность кода (manifest↔schemas↔handler↔client), PST реально
**вызывает** код через `imperal_sdk.testing.MockContext` и ловит то, что
статическая сверка структурно не видит.

Все 6 обязательных слоёв (Слой 1 статический пост-аудит + Слой 2 PST 5-branch
+ Часть D целиком: D1 Deploy Verification / D2 Idempotency / D3 Security /
D4 Regression grep) прогнаны по умолчанию, без отдельного запроса
пользователя — решение зафиксировано 2026-08-19/20.

---

## Прогон 2026-08-20

**Почему через MockContext.** У пользователя нет подключённого BYOK Power
Platform окружения. Реальные вызовы `handlers.py`/`power_automate_client.py`
(тот же код, что исполняется в продакшене) через официальный
`imperal_sdk.testing.MockContext` + `MockHTTP`, подставляя контролируемые
ответы, соответствующие реальному REST-контракту Dataverse Web API
(`/api/data/v9.2/workflows`) и Power Platform admin API для flow runs.

**Персона.** Одна функциональная роль: "Marina", agency ops lead, владелец
Azure AD App Registration + Application User против собственного Dataverse
окружения. Разнообразие сценариев — из классов данных (пустое/типичное/
пограничное/невалидное/экзотическое состояние окружения — Draft vs Activated
vs Suspended, Solution vs "My Flows", 401/403/404/429/5xx), не из персон.

**Харнесс:** `tests/conftest.py` (`ctx`, `ctx_connected` fixtures),
`tests/test_pst_scenarios.py` — 23 теста.

### Слой 1 — статический пост-аудит

AST-проверка находила 4 handler'а (`connect_power_automate`, `get_flow`,
`create_flow`, `update_flow`, `get_flow_run`) как "MISMATCH" между
`data_model` и сконструированной сущностью — ложное срабатывание: чекер не
разворачивает вызовы helper-функций (`_connection_entity`, `_flow_entity`,
`_run_entity`). Ручная проверка через grep подтвердила: все три helper'а
корректно строят и возвращают задекларированный класс. **0 реальных
расхождений.**

### Слой 2 — PST, 5 обязательных веток

1. **Happy path** — connect (сохраняет 5 полей), list/get/create/update/
   delete flow, set/bulk-set state, list/get/cancel/resubmit flow run,
   bulk delete.
2. **Error** — неверные креды (401, credentials НЕ сохраняются), валидный
   токен но нет Dataverse-роли (403, permission_denied), 404 not-found
   (отличимо от generic-ошибки), 429 rate-limited (помечено retryable),
   malformed clientdata JSON (400 validation_failed).
3. **Blocked** — нет подключения → actionable-ошибка; неоднозначный
   connection_id при двух подключённых окружениях.
4. **Recovery** — set_flow_state: первый вызов ловит 500, второй (retry)
   проходит чисто, без остаточного состояния.
5. **Adversarial** — bulk-операции изолируют успех/неудачу по item'ам;
   пустой run_id не крашит; ровно 100 id (граница) — все обработаны без
   silent truncation.

### Реальные баги, найденные и исправленные в этом прогоне

1. **`resp.status` вместо `resp.status_code` (13 мест, `power_automate_client.py`).**
   Реальный (не мок) `imperal_sdk` HTTPResponse не имеет атрибута `status` —
   только `status_code`. Это сломало бы КАЖДЫЙ реальный HTTP-вызов в
   продакшене (import-only проверки типа `ast.parse`/валидация манифеста
   этот путь не выполняют). **Исправлено:** все 13 вхождений заменены.

2. **10 из 11 `sdl.Entity`-сущностей в `schemas.py` не объявляли `title: str`
   без дефолта (`Entity` требует его обязательным).** Каждый успешный
   `ActionResult` во всём приложении упал бы с `ValidationError` в
   продакшене. **Исправлено:** добавлен `title: str = ""` + осмысленные
   `title=` на местах конструирования (не голая пустая строка).

3. **`BulkFlowResultItem`, `BulkFlowResult`, `ProviderConnectionList`,
   `PowerAutomateFlowList`, `PowerAutomateFlowRunList` не объявляли `id`
   (тоже обязательное поле `Entity`).** Всплыло только при реальном вызове
   через PST — статический пост-аудит и предыдущий этап фиксов это не
   поймали. **Исправлено:** добавлен `id: str = ""` (для списковых сущностей
   — синтетический стабильный id) + `id=` на местах конструирования
   bulk-результатов.

4. **`delete_flow`/`bulk_delete_flows` классифицированы как
   `action_type="write"` вместо `"destructive"`**, хотя описаны как
   необратимые ("Cannot be undone", без recovery/trash) — расхождение с
   конвенцией портфеля (сравнение с n8n `delete_n8n_workflow`,
   Make.com `delete_scenario` которое recoverable → `"write"` корректно).
   **Исправлено:** оба переклассифицированы на `"destructive"`.

5. **`connect_power_automate` не дедуплицировал по `environment_url`** —
   повторное подключение того же окружения (например, после ротации
   client_secret) создавало ВТОРУЮ отдельную запись вместо замены
   существующей, что сделало бы последующий `_resolve_connection` без
   явного `connection_id` неоднозначным. Найдено PST-тестом двойного
   `connect`. **Исправлено:** ищем существующую запись по
   `environment_url`, заменяем её (сохраняя старый `id`), а не append.

### Часть D

**D1 (Deploy Verification):** git-репозиторий инициализирован
(`github.com/ivanco-bluebeeweb-com/power-automate-connector`), запушен;
приложение зарегистрировано (`create_app`) и задеплоено (`deploy_app`) —
дважды подряд, идентичный commit/статус оба раза. `manifest_synced: true`,
`panels_synced: true`, `icon_synced: true`, `validation: 19/21`.
**Платформенная аномалия:** `tools_synced: 3` при 15 реальных
`@chat.function` — подтверждена как кросс-портфельная (n8n и Make.com дают
тот же "3" при их 60+ функциях каждый). Задокументировано как новое
доказательство в существующем таске #2125 (проект "Imperal Cloud", НЕ
личная доска пользователя "BBW Imperal Apps") — не создан дубликат.

**D2 (Idempotency):** двойной `disconnect_power_automate` — второй вызов
даёт специфичный not-found, не крашится и не молчаливо "успешен". Двойной
`delete_flow` — второй вызов на уже удалённый workflow_id получает
специфичный not-found от Dataverse (404), не ложный повторный успех.

**D3 (Security) — BYOK-прецедент, как у n8n/Make.com.** Классический
SSRF-фильтр (резолв IP, блокировка приватных адресов) НЕ применяется:
приложение по дизайну обращается к СОБСТВЕННОМУ Dataverse-окружению
пользователя по адресу, который он сам сохранил — блокировка сломала бы
легитимные VPN/private-network окружения. Вместо этого: grep на утечку
`client_secret` через побочные поля (`label`/`title`/`description`/
`summary`) — 0 совпадений, чисто. `_connection_entity` подтверждённо не
эхо-ит `client_secret` ни в одном поле.

**D4 (Regression grep):** прогнаны все известные записи
`Docs/known-bug-patterns.md` против кода этого приложения:
- `.pop(field, None)` before `store.update()` — 0 совпадений (только
  безобидный `sys.modules.pop` в `main.py`).
- SSRF/raw-fetch на пользовательский домен — не применимо (BYOK, см. D3).
- Секрет через побочное поле — 0 совпадений (см. D3).
- **Новый паттерн, добавленный этим прогоном** (см. `known-bug-patterns.md`):
  `resp.status` vs `resp.status_code` + отсутствующие обязательные поля
  `Entity` (`id`/`title`) без дефолта.

### Итог прогона

```
23 passed in <1s
```

5 реальных production-breaking багов найдено и исправлено. Приложение
закоммичено, запушено, задеплоено дважды (идентичный результат — деплой
сам по себе идемпотентен).
