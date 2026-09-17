# Programa el respaldo automático de la base de datos todos los días a las 03:00.
# Ejecutar una vez con PowerShell:  powershell -ExecutionPolicy Bypass -File programar_respaldo.ps1
$Proyecto = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "C:\Users\escal\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $Python)) {
    $resolver = Get-Command python -ErrorAction SilentlyContinue
    if ($resolver) { $Python = $resolver.Source } else { Write-Error "No se encontró Python"; exit 1 }
}
$Accion = New-ScheduledTaskAction -Execute $Python -Argument "`"$Proyecto\respaldar.py`"" -WorkingDirectory $Proyecto
$Gatillo = New-ScheduledTaskTrigger -Daily -At 3:00AM
$Config = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "PollosLucho_Respaldo" -Action $Accion -Trigger $Gatillo -Settings $Config -Description "Respaldo diario de pollos_lucho (3:00 AM)" -Force
Write-Host "Respaldo diario programado: PollosLucho_Respaldo (03:00)."
Write-Host "RECOMENDACION: copia semanal de los .sql a otra maquina/nube (copia externa)."