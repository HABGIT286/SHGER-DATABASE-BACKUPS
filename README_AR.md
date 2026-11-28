# NOIR Control — GitHub Native (بدون Backend خارجي)

## ما في الحزمة؟
- `index.html`: لوحة ثابتة HTML/CSS/JS. لا أرقام تجريبية ولا API keys ولا CORS.
- `.github/workflows/noir-control.yml`: workflow dispatcher ينفذ start/stop/restart عبر GitHub Actions باستخدام `github.token`.

## التثبيت
1. انقل `index.html` إلى جذر المستودع أو إلى `/docs` (حسب إعداد Pages).
2. انقل `.github/workflows/noir-control.yml` إلى مسار workflows في مستودعك.
3. في Workflow bot الحالي، تأكد أن الاسم مطابق تمامًا: `ملف تشغيل v6.yml` وأن فرع التشغيل `main`. إذا كان اسم workflow أو branch مختلفًا، عدّل المتغير `TARGET` و`branch=main` و`ref=main` في ملف `noir-control.yml`.
4. افتح Repository Settings → Pages، اختر النشر من branch `main` وحدد root أو docs حسب مكان index.html.
5. افتح GitHub Actions → NOIR Control Dispatcher → Run workflow. اختر start/stop/restart ثم نفذ.

## حدود مهمّة بصدق
GitHub Pages موقع static؛ لا يملك صلاحية API مخفية ولا يستطيع تنفيذ Workflow بأمان من JavaScript. لهذا، هذه اللوحة تعرض روابط GitHub مباشرة. تشغيل/إيقاف/إعادة تشغيل يتم من صفحة Actions عبر dispatcher، ويحتاج أن تكون مسجل دخول بصلاحيات repo. لا يوجد تحقق `WEB_ADMIN_PASSWORD` في واجهة static لأن كلمة المرور لا يمكن حمايتها بدون server-side service. حالات bot live وCPU/RAM ومستخدمين لا تعرضها اللوحة ما لم يتم إضافة مصدر بيانات حقيقي.

Workflow dispatcher يحتاج صلاحية Actions write من إعدادات Actions، ويستخدم token المدمج فقط ولا يطلب PAT.
