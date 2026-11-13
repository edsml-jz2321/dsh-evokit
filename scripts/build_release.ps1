$ErrorActionPreference = 'Stop'
# Run from the repository root with uv, Node and npm available.
uv sync --frozen --group dev
if ($LASTEXITCODE) { exit $LASTEXITCODE }
uv build
if ($LASTEXITCODE) { exit $LASTEXITCODE }
uv run --frozen python scripts/check_package.py
if ($LASTEXITCODE) { exit $LASTEXITCODE }
npm pack ./integrations/dsh --ignore-scripts --pack-destination dist
if ($LASTEXITCODE) { exit $LASTEXITCODE }
uv run --frozen python integrations/dsh/check_package.py
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$sums = Get-ChildItem dist -File | Where-Object { $_.Name -match '\.(whl|tar\.gz|tgz)$' } | Sort-Object Name | ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower() + '  ' + $_.Name }
[IO.File]::WriteAllText((Join-Path (Get-Location) 'dist/SHA256SUMS'), (($sums -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
