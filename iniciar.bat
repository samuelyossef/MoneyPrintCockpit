@echo off
title Moneyprint Cockpit
echo =======================================================
echo  Iniciando Gateway (Docker Compose)...
echo =======================================================
docker compose up -d --wait
if errorlevel 1 (
  echo.
  echo Falha ao iniciar. Rode "powershell -File setup.ps1" primeiro se ainda nao configurou,
  echo ou veja os logs com: docker compose logs
  pause
  exit /b 1
)
start http://localhost:3000
echo Gateway em execucao em http://localhost:3000
echo Feche esta janela normalmente; para parar os containers rode: docker compose down
pause
