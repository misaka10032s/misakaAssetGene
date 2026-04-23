$env:MISAKA_ENV = if ($env:MISAKA_ENV) { $env:MISAKA_ENV } else { "dev" }
$env:VITE_MISAKA_ENV = if ($env:VITE_MISAKA_ENV) { $env:VITE_MISAKA_ENV } else { "dev" }

& .\.venv\Scripts\python.exe .\scripts\dev_stack.py
