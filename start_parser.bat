@echo off
echo ========================================
echo    Avito Parser - CLI Mode
echo ========================================
echo.

REM Проверяем наличие виртуального окружения
if not exist "venv\Scripts\python.exe" (
    echo ОШИБКА: Виртуальное окружение не найдено!
    echo Создайте его командой: python -m venv venv
    echo Затем установите зависимости: venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Выберите режим работы:
echo 1. Парсинг по URL (live)
echo 2. Парсинг локального файла
echo 3. Показать последние записи из БД
echo 4. Выход
echo.

set /p choice="Введите номер (1-4): "

if "%choice%"=="1" goto live_parse
if "%choice%"=="2" goto file_parse
if "%choice%"=="3" goto show_db
if "%choice%"=="4" goto exit
echo Неверный выбор!
pause
goto :eof

:live_parse
echo.
set /p url="Введите URL для парсинга: "
if "%url%"=="" (
    echo URL не может быть пустым!
    pause
    goto :eof
)
echo Запуск парсинга по URL: %url%
venv\Scripts\python.exe -m src.main --url "%url%"
goto end

:file_parse
echo.
echo Поиск файлов для парсинга...
if exist "Trash\page.html" (
    echo Найден файл: Trash\page.html
    venv\Scripts\python.exe -m src.main parse-file "Trash\page.html"
) else if exist "page_pretty.html" (
    echo Найден файл: page_pretty.html
    venv\Scripts\python.exe -m src.main parse-file "page_pretty.html"
) else if exist "page.txt" (
    echo Найден файл: page.txt
    venv\Scripts\python.exe -m src.main parse-file "page.txt"
) else (
    echo Файлы для парсинга не найдены!
    echo Ожидаемые файлы: Trash\page.html, page_pretty.html, page.txt
)
goto end

:show_db
echo.
set /p count="Сколько записей показать (по умолчанию 10): "
if "%count%"=="" set count=10
venv\Scripts\python.exe -m src.main dump --count %count%
goto end

:exit
echo До свидания!
goto :eof

:end
echo.
echo Операция завершена.
pause
