@echo off
chcp 65001 >nul
title 无限画布 - 一键环境安装

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║     🎨 无限画布 - 环境安装向导            ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ==================== Node.js 检查 ====================
echo  [1/5] 检查 Node.js...
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo  ❌ 未找到 Node.js，请先安装: https://nodejs.org
    echo     下载 LTS 版本，安装后重新运行本脚本
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('node --version') do set NODE_VER=%%i
echo  ✅ Node.js %NODE_VER%

:: ==================== npm 依赖 ====================
echo.
echo  [2/5] 安装 Node.js 依赖包...
call npm install --loglevel=error
if %errorlevel% neq 0 (
    echo  ❌ npm install 失败，请检查网络连接
    pause
    exit /b 1
)
echo  ✅ npm 依赖安装完成

:: ==================== Python 检查 ====================
echo.
echo  [3/5] 检查 Python...
where python >nul 2>&1
if %errorlevel% neq 0 (
    where python3 >nul 2>&1
    if %errorlevel% neq 0 (
        echo  ⚠️ 未找到 Python 3.10+（爬虫功能需要）
        echo     下载地址: https://www.python.org/downloads/
        echo     安装时勾选 "Add Python to PATH"
        echo     无 Python 也可启动服务，但网页爬取将降级为原生请求
        goto :skip_python
    )
    set PYTHON_CMD=python3
) else (
    set PYTHON_CMD=python
)

for /f "tokens=*" %%i in ('%PYTHON_CMD% --version 2^>^&1') do set PY_VER=%%i
echo  ✅ %PY_VER%

:: 检查 Python 版本 >= 3.10
%PYTHON_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo  ⚠️ Python 版本过低（需要 3.10+），爬虫功能将降级
    goto :skip_python
)

:: ==================== Python 依赖 ====================
echo.
echo  [4/5] 安装 Python 依赖（Scrapling 爬虫框架）...
%PYTHON_CMD% -m pip install -r requirements.txt --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo  ⚠️ Scrapling 安装失败，爬虫功能将降级为原生请求
    goto :skip_python
)
echo  ✅ Scrapling 安装完成

:: 安装浏览器依赖（用于JS渲染页面，约300MB）
echo  安装浏览器依赖（约 300MB，首次需等待）...
where scrapling >nul 2>&1
if %errorlevel% equ 0 (
    scrapling install >nul 2>&1
) else (
    :: scrapling CLI 不在PATH，通过 playwright 安装
    %PYTHON_CMD% -m playwright install chromium >nul 2>&1
)
if %errorlevel% equ 0 (
    echo  ✅ 浏览器依赖安装完成
) else (
    echo  ⚠️ 浏览器依赖跳过（仅影响需JS渲染的网站）
)

:skip_python

:: ==================== 启动服务 ====================
echo.
echo  [5/5] 启动服务...
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║  所有依赖就绪，正在启动...                ║
echo  ╚══════════════════════════════════════════╝
echo.
node server.js

pause
