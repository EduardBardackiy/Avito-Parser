@echo off
echo ========================================
echo    Остановка Avito Parser Bot
echo ========================================
echo.

echo Поиск запущенных экземпляров Python...
tasklist /fi "imagename eq python.exe" 2>nul | find /i "python.exe" >nul
if not errorlevel 1 (
    echo Найдены запущенные процессы Python:
    tasklist /fi "imagename eq python.exe"
    echo.
    echo Останавливаем все процессы Python...
    taskkill /f /im python.exe
    if not errorlevel 1 (
        echo Все процессы Python остановлены.
    ) else (
        echo Не удалось остановить некоторые процессы.
    )
) else (
    echo Запущенные процессы Python не найдены.
)

echo.
echo Очистка завершена.
pause
