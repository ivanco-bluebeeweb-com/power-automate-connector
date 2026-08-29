# Power Automate Connector — идеальный первый запуск

Источник: `ONBOARDING_FIRST_LAUNCH_STANDARD.md`. Целевой пользователь: Ops/IT-
специалист на экосистеме Microsoft 365 (Power Automate).

## 1. Credential type
Azure AD App Registration (tenant_id + client_id + client_secret) — трёхкомпонентный
OAuth client-credentials, специфичный для Microsoft-стека.

## 2. Идеальный флоу
1. **Первое открытие** — `Empty` со ссылкой на регистрацию Azure AD App + явный список
   нужных API permissions (Power Platform API scope) — Microsoft-стек известен сложной
   настройкой permissions, максимально подробная пошаговая инструкция здесь критична.
2. **Форма** — tenant_id + client_id + client_secret, все три с лейблами (Azure GUID-
   формат виден по placeholder).
3. **Environment selector** — Power Platform организован по Environments внутри
   tenant — идеально: селектор Environment сразу после успешной аутентификации.
4. **После выбора Environment** — список flows со статусом (включён/выключен) и
   последним запуском сразу.
5. **Ошибка "admin consent required"** — Azure AD часто требует явного admin consent
   на permissions — конкретное сообщение с прямой ссылкой на consent URL.

## 3. Разница с реализацией сейчас
См. `UI_COMPONENT_PLAN.md` §0.
