# Скрипт автоматической сборки релиза Refer
Write-Host "--- [1/3] Запуск PyInstaller (сборка кода) ---" -ForegroundColor Cyan
& .venv/Scripts/python.exe -m PyInstaller --noconfirm build.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Ошибка при сборке PyInstaller!" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "--- [2/3] Создание инсталлятора (Inno Setup) ---" -ForegroundColor Cyan
# Ищем компилятор Inno Setup по стандартному пути
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc /Q installer.iss
} else {
    Write-Host "⚠️ Компилятор ISCC.exe не найден. Пожалуйста, соберите installer.iss вручную в Inno Setup." -ForegroundColor Yellow
}

Write-Host "--- [3/3] Очистка временных файлов ---" -ForegroundColor Cyan
if (Test-Path "build") { Remove-Item -Path "build" -Recurse -Force }

Write-Host "✅ ГОТОВО! Ваш новый инсталлятор в папке 'setup' или 'dist'." -ForegroundColor Green
