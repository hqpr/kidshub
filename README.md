# Тиц!

Мінімалістичний сервіс опитувань українською: покрокові форми з одним або кількома варіантами чи текстовою відповіддю, відновлення незавершеної анкети, необов'язкове ім'я та статистика кожного питання для адміністратора.

## Локальний запуск

Створіть базу PostgreSQL `kidshub` і, якщо ваші локальні реквізити відрізняються, змініть `DATABASE_URL` у `.vscode/launch.json`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install --prefix frontend
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/kidshub ADMIN_KEY=ваш-довгий-секретний-ключ uvicorn backend.main:app --reload
npm run dev --prefix frontend
```

Відкрийте `http://localhost:5173/panel-22652d9c5949747d27ba/`. Для VS Code також є compound-конфігурація `Тиц!: усе разом`; її локальний ключ — `tyts-local-7f29a4c8`.

Після `npm run build --prefix frontend` FastAPI сам віддає зібраний інтерфейс на `http://localhost:8000`.

Шлях адмінки задається у `frontend/.env` через `VITE_ADMIN_PATH`. Це приховує вхід із навігації, але не замінює ключ: у production використовуйте унікальний `ADMIN_KEY` від 12 символів і HTTPS.

## Railway

1. Створіть проєкт із GitHub-репозиторію. Кореневий `Dockerfile` збере Vue та запустить FastAPI.
2. Додайте в той самий проєкт сервіс PostgreSQL.
3. У Variables вебсервісу додайте `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `ADMIN_KEY` із довгим випадковим значенням і `VITE_ADMIN_PATH` зі шляхом адмінки.
4. У Settings → Networking згенеруйте публічний домен.
5. У Settings → Deploy задайте Healthcheck Path: `/health`.

Щоб один раз перенести локальні опитування й відповіді з `kidshub.db`, увімкніть Public Access у Networking сервісу PostgreSQL, скопіюйте `DATABASE_PUBLIC_URL` і локально виконайте:

```bash
DATABASE_PUBLIC_URL='postgresql://…' python -m backend.migrate_sqlite
```

Імпортер працює лише з порожньою PostgreSQL-базою. Після успішного перенесення Public Access бази можна вимкнути: застосунок використовує приватний `DATABASE_URL`.

## Перевірка

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/kidshub_test python -m unittest backend.test_main
npm run build --prefix frontend
```
