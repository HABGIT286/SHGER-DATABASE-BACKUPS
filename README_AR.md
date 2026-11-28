# NOIR Control — مدارك المعرفة

هذه النسخة تربط لوحة NOIR بملفات بوت مدارك المعرفة الأصلية وتحتفظ بكود البوت ووظائفه. لا تُعرض إحصاءات افتراضية ولا تُخزّن مفاتيح Telegram/GitHub في واجهة الموقع.


## الملفات

- `index.html` — موقع لوحة التحكم الثابتة. تبويبات وظائف البوت، روابط GitHub، تخصيص المظهر والخطوط، وملاحظات محلية.
- `bot.py` — كود البوت المأخوذ من ملف المستخدم، مع إعداد `GROUP_LINK` و`DEV_TELE` عبر البيئة.
- `.github/workflows/noir-control.yml` — Workflow dispatcher لبدء/إيقاف/إعادة تشغيل Workflow البوت.
- `ملف تشغيل v6.yml` — تشغيل البوت الأساسي وجدولة التشغيل.
- `requirements.txt` — مكتبات Python المطلوبة.


## طريقة وضع الملفات بالمستودع

1. ضع `index.html` في جذر المستودع الذي ينشر GitHub Pages.
2. ضع `bot.py` و`requirements.txt` في جذر المستودع.
3. ضع `ملف تشغيل v6.yml` داخل `.github/workflows/` (اسم الملف يجب أن يبقى كما هو لأن Dispatcher يشير إليه).
4. ضع `noir-control.yml` داخل `.github/workflows/`.
5. فعّل GitHub Pages من Settings → Pages حسب الفرع ومجلد النشر.
6. تأكد أن Workflow permissions تسمح للـ Actions بالكتابة إلى محتويات المستودع حتى يحفظ `library_index.json`.


## Actions Variables

ضع القيم غير الحساسة التالية في Settings → Secrets and variables → Actions → Variables:

- `JOIN_CHANEL` — معرف قناة Telegram أو `@username`.
- `ID_TELE` — معرف حساب مالك البوت.
- `DEV_TELE` — اسم حساب التواصل دون/مع `@`.
- `GROUP_LINK` — رابط قناة المكتبة الذي يظهر للمستخدمين.
- `WELCOME_IMAGE` — رابط صورة الترحيب (اختياري).


## Actions Secrets

ضع القيم السرية في Settings → Secrets and variables → Actions → Secrets:

- `TOKENN` — توكن Telegram bot.
- `PROXY_URL` — اختياري، رابط Proxy.

لا تحتاج إلى إنشاء `GITHUB_TOKEN` يدويًا؛ Workflow يمرر `github.token` إلى البوت. تأكد من أن صلاحيات الـ Workflow تسمح بـ `contents: write`.


## التحكم والتشغيل

- من الموقع، افتح GitHub Actions ثم Workflow `NOIR Control Dispatcher`.
- اختر `Run workflow`، وحدد `start` أو `stop` أو `restart`.
- افتح Workflow البوت لمراجعة سجل التشغيل.
- الجدولة الحالية في Workflow البوت كل ست ساعات، وGitHub Actions ليست خدمة استضافة دائمة؛ مدة تشغيل البوت محدودة بسقف تشغيل الـ runner.


## ملاحظات أمان وقيود واقعية

- GitHub Pages موقع static؛ لا يمكنه التحقق بأمان من `WEB_ADMIN_PASSWORD` المخزّن في Actions Variables، ولا يمكنه إرسال أوامر Actions مباشرة دون آلية مصادقة موثوقة.
- لا تضع `WEB_ADMIN_PASSWORD` أو `GH_TOKEN` أو `TOKENN` في HTML/JavaScript. Variables ليست مخزنًا مناسبًا للبيانات السرية.
- لوحة NOIR تفتح صفحات GitHub المناسبة ولا تدعي أن الحالة Live. إحصائيات المكتبة الحقيقية تُقرأ من البوت أو من الفهرس وسجل Actions.
- الملاحظات في الموقع تُحفظ محليًا على المتصفح فقط ولا تُزامن إلى GitHub.
