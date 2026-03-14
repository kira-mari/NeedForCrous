param(
    [ValidateSet('Interactive', 'Verbose', 'Quiet')]
    [string]$Mode = 'Interactive',
    [switch]$NoPause,
    [switch]$IncludeEnv,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$LegacyArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# In PowerShell 7+, some native tools write informational lines to stderr.
# Keep those lines from being treated as terminating errors.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptRoot

$LogFile = Join-Path $ScriptRoot 'build_exe.log'
$BuildName = 'NeedForCrous'
$ExePath = Join-Path $ScriptRoot "dist\\$BuildName.exe"
$EnvFilePath = Join-Path $ScriptRoot '.env'
$EnvTemplatePath = Join-Path $ScriptRoot '.env.template'

$colorPrimary = 'White'
$colorSecondary = 'DarkGray'
$colorSelected = 'Red'
$colorIdle = 'DarkGray'
$colorImportant = 'Red'
$colorWarning = 'Yellow'
$colorSuccess = 'Green'

function Show-Help {
    Write-Host ''
    Write-Host 'Usage principal:' -ForegroundColor $colorPrimary
    Write-Host '  .\build_exe.ps1 [-Mode Interactive|Verbose|Quiet] [-NoPause]' -ForegroundColor $colorSecondary
    Write-Host ''
    Write-Host 'Raccourcis:' -ForegroundColor $colorPrimary
    Write-Host '  .\build_exe.ps1 --verbose' -ForegroundColor $colorSecondary
    Write-Host '  .\build_exe.ps1 --quiet' -ForegroundColor $colorSecondary
    Write-Host '  .\build_exe.ps1 --include-env' -ForegroundColor $colorSecondary
    Write-Host '  .\build_exe.ps1 --help' -ForegroundColor $colorSecondary
    Write-Host ''
    Write-Host 'Modes:' -ForegroundColor $colorPrimary
    Write-Host '  Interactive : menu dynamique (lecture clavier instantanee V/Q)' -ForegroundColor $colorSecondary
    Write-Host '  Verbose     : logs complets en console' -ForegroundColor $colorSecondary
    Write-Host '  Quiet       : interface wizard + progression + log fichier' -ForegroundColor $colorSecondary
    Write-Host '  IncludeEnv  : copie .env vers dist/.env (sinon copie .env.template)' -ForegroundColor $colorSecondary
    Write-Host ''
}

function Write-Panel {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [AllowEmptyCollection()][string[]]$Lines = @(),
        [int]$Width = 78
    )

    $inner = $Width - 4
    $h = [string]([char]0x2500)
    $top = ([char]0x250C) + ($h * ($Width - 2)) + ([char]0x2510)
    $mid = ([char]0x251C) + ($h * ($Width - 2)) + ([char]0x2524)
    $bottom = ([char]0x2514) + ($h * ($Width - 2)) + ([char]0x2518)

    Write-Host $top -ForegroundColor $colorSecondary
    Write-Host (([char]0x2502) + ' ' + $Title.PadRight($inner) + ' ' + ([char]0x2502)) -ForegroundColor $colorPrimary
    Write-Host $mid -ForegroundColor $colorSecondary
    if ($Lines.Count -eq 0) {
        $Lines = @(' ')
    }

    foreach ($line in $Lines) {
        $lineText = if ($null -eq $line -or $line -eq '') { ' ' } else { [string]$line }
        $safe = if ($lineText.Length -gt $inner) { $lineText.Substring(0, $inner) } else { $lineText }
        Write-Host (([char]0x2502) + ' ' + $safe.PadRight($inner) + ' ' + ([char]0x2502)) -ForegroundColor $colorSecondary
    }
    Write-Host $bottom -ForegroundColor $colorSecondary
}

function Get-Bar {
    param(
        [int]$Percent,
        [int]$Length = 30,
        [string]$FilledChar = '#',
        [string]$EmptyChar = '-'
    )
    $filled = [Math]::Floor($Length * ($Percent / 100.0))
    if ($filled -lt 0) { $filled = 0 }
    if ($filled -gt $Length) { $filled = $Length }
    return ($FilledChar * $filled) + ($EmptyChar * ($Length - $filled))
}

function Write-BoxLine {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [int]$Width = 78,
        [string]$TextColor = 'DarkRed',
        [string]$BorderColor = 'Red'
    )

    $inner = $Width - 4
    $safe = if ($Text.Length -gt $inner) { $Text.Substring(0, $inner) } else { $Text }
    $v = [char]0x2503
    Write-Host ("$v ") -ForegroundColor $BorderColor -NoNewline
    Write-Host $safe.PadRight($inner) -ForegroundColor $TextColor -NoNewline
    Write-Host (" $v") -ForegroundColor $BorderColor
}

function Parse-EnvMap {
    param([string]$Path)

    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }

    foreach ($line in Get-Content $Path -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
            $map[$matches[1]] = $matches[2]
        }
    }
    return $map
}

function Get-EnvHint {
    param([string]$Key)

    switch ($Key) {
        'MSE_EMAIL' { return 'Email Messervices.etudiant.gouv.fr (ex: prenom.nom@mail.com)' }
        'MSE_PASSWORD' { return 'Mot de passe Messervices.etudiant.gouv.fr' }
        'TELEGRAM_BOT_TOKEN' { return 'Token du bot Telegram (obtenu via @BotFather)' }
        'MY_TELEGRAM_ID' { return 'Ton ID Telegram (obtenu via @userinfobot)' }
        'SEARCH_URL' { return 'URL de recherche TrouverUnLogement (copie depuis ton navigateur)' }
        'DATE_FROM' { return 'Optionnel - Date debut YYYY-MM-DD (laisser vide si inutile)' }
        'DATE_TO' { return 'Optionnel - Date fin YYYY-MM-DD (laisser vide si inutile)' }
        default { return 'Renseigne une valeur valide' }
    }
}

function Convert-SecureStringToPlainText {
    param([Parameter(Mandatory = $true)][Security.SecureString]$SecureValue)

    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
}

function Ensure-EnvConfiguration {
    if (-not (Test-Path $EnvTemplatePath)) {
        throw ".env.template introuvable: $EnvTemplatePath"
    }

    if (-not (Test-Path $EnvFilePath)) {
        Copy-Item -Path $EnvTemplatePath -Destination $EnvFilePath -Force
        Write-Host ".env absent: creation depuis .env.template" -ForegroundColor $colorSecondary
    }

    $templateLines = Get-Content $EnvTemplatePath
    $envMap = Parse-EnvMap -Path $EnvFilePath
    $placeholders = @{
        'MSE_EMAIL' = 'votre_email@exemple.com'
        'MSE_PASSWORD' = 'votre_mot_de_passe'
        'TELEGRAM_BOT_TOKEN' = '123456789:ABCdefGHIjklMNOpqrsTUVwxyz'
        'MY_TELEGRAM_ID' = '987654321'
        'SEARCH_URL' = 'https://trouverunlogement.lescrous.fr/tools/36/search?bounds=...'
    }
    $optionalKeys = @('DATE_FROM', 'DATE_TO')

    foreach ($line in $templateLines) {
        if ($line -notmatch '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
            continue
        }

        $key = $matches[1]
        $current = ''
        if ($envMap.ContainsKey($key)) {
            $current = [string]$envMap[$key]
        }

        $needsPrompt = [string]::IsNullOrWhiteSpace($current)
        if (-not $needsPrompt -and $placeholders.ContainsKey($key) -and $current -eq $placeholders[$key]) {
            $needsPrompt = $true
        }

        if (-not $needsPrompt) {
            continue
        }

        Write-Host ''
        Write-Host ("Variable: {0}" -f $key) -ForegroundColor $colorPrimary
        Write-Host ("Info    : {0}" -f (Get-EnvHint -Key $key)) -ForegroundColor $colorSecondary

        while ($true) {
            if ($key -eq 'MSE_PASSWORD') {
                $secureValue = Read-Host ("{0}" -f $key) -AsSecureString
                $value = Convert-SecureStringToPlainText -SecureValue $secureValue
            }
            else {
                $value = Read-Host ("{0}" -f $key)
            }

            if ($optionalKeys -contains $key) {
                $envMap[$key] = $value
                break
            }

            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $envMap[$key] = $value
                break
            }

            Write-Host 'Valeur obligatoire. Merci de renseigner cette variable.' -ForegroundColor $colorImportant
        }
    }

    $newLines = @()
    foreach ($line in $templateLines) {
        if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
            $key = $matches[1]
            $value = if ($envMap.ContainsKey($key)) { [string]$envMap[$key] } else { '' }
            $newLines += ("{0}={1}" -f $key, $value)
        }
        else {
            $newLines += $line
        }
    }

    Set-Content -Path $EnvFilePath -Value $newLines -Encoding UTF8
}

function Show-ModeMenu {
    $options = @(
        [pscustomobject]@{ Label = 'Mode VERBOSE  - commandes detaillees et logs complets'; Value = 'Verbose' },
        [pscustomobject]@{ Label = 'Mode QUIET    - interface design + barre de progression'; Value = 'Quiet' }
    )
    $selection = 0

    function Show-Menu {
        Clear-Host
        Write-Host '                                                                             ' -ForegroundColor $colorImportant
        Write-Host '   ▄▄     ▄▄▄                  ▄▄▄▄▄▄▄        ▄   ▄▄▄▄                       ' -ForegroundColor $colorImportant
        Write-Host '   ██▄   ██▀               █▄ █▀██▀▀▀         ▀██████▀                       ' -ForegroundColor $colorImportant
        Write-Host '   ███▄  ██                ██   ██        ▄     ██     ▄                     ' -ForegroundColor $colorImportant
        Write-Host '   ██ ▀█▄██ ▄█▀█▄ ▄█▀█▄ ▄████   ███▀▄███▄ ████▄ ██     ████▄▄███▄ ██ ██ ▄██▀█' -ForegroundColor $colorImportant
        Write-Host '   ██   ▀██ ██▄█▀ ██▄█▀ ██ ██ ▄ ██  ██ ██ ██    ██     ██   ██ ██ ██ ██ ▀███▄' -ForegroundColor $colorImportant
        Write-Host ' ▀██▀    ██▄▀█▄▄▄▄▀█▄▄▄▄█▀███ ▀██▀ ▄▀███▀▄█▀    ▀█████▄█▀  ▄▀███▀▄▀██▀██▄▄██▀' -ForegroundColor $colorImportant
        Write-Host '                                                                             ' -ForegroundColor $colorImportant
        Write-Host '                                                                             ' -ForegroundColor $colorImportant
        Write-Host "Navigation : [↑][↓]   Validation : [ENTER]   Quitter : [ESC]" -ForegroundColor $colorPrimary
        Write-Host ""

        for ($i = 0; $i -lt $options.Count; $i++) {
            if ($i -eq $selection) {
                Write-Host " ┃ " -ForegroundColor $colorSelected -NoNewline
                Write-Host $options[$i].Label -ForegroundColor $colorPrimary
            }
            else {
                Write-Host " | " -ForegroundColor $colorIdle -NoNewline
                Write-Host $options[$i].Label -ForegroundColor $colorSecondary
            }
        }

        Write-Host ""
        Write-Host "  Astuce: Tu peux aussi appuyer directement sur V ou Q." -ForegroundColor $colorSecondary
    }

    while ($true) {
        Show-Menu

        $key = $null
        $char = ''

        try {
            $key = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
            $char = [string]$key.Character
        }
        catch {
            # Some hosts (non-interactive terminals, CI) do not expose RawUI.ReadKey.
            $fallback = Read-Host 'Ton choix [V/Q]'
            if ($fallback) {
                $char = $fallback.Trim().Substring(0, 1)
            }
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 27) {
            throw 'Annule par utilisateur.'
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 38) {
            if ($selection -gt 0) {
                $selection--
            }
            continue
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 40) {
            if ($selection -lt ($options.Count - 1)) {
                $selection++
            }
            continue
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 13) {
            return $options[$selection].Value
        }

        if ($char) {
            $pick = $char.ToUpperInvariant()
            if ($pick -eq 'V') {
                return 'Verbose'
            }
            if ($pick -eq 'Q') {
                return 'Quiet'
            }
        }

        Write-Host ""
        Write-Host "Touche invalide. Utilise UP, DOWN, ENTER, ou V/Q, ou ESC." -ForegroundColor $colorWarning
        Start-Sleep -Milliseconds 500
    }
}

function Ask-IncludeEnvChoice {
    $options = @(
        [pscustomobject]@{ Label = 'Copier .env (contient des secrets)'; Value = $true; Color = $colorWarning },
        [pscustomobject]@{ Label = 'Copier .env.template (recommande, plus sur)'; Value = $false; Color = $colorSecondary }
    )

    # Default safe choice: .env.template
    $selection = 1

    function Show-EnvCopyMenu {
        Clear-Host
        Write-Host 'Copie de configuration dans dist' -ForegroundColor $colorPrimary
        Write-Host 'Navigation : [↑][↓]   Validation : [ENTER]   Quitter : [ESC]' -ForegroundColor $colorSecondary
        Write-Host ''

        for ($i = 0; $i -lt $options.Count; $i++) {
            if ($i -eq $selection) {
                Write-Host ' ┃ ' -ForegroundColor $colorSelected -NoNewline
                Write-Host $options[$i].Label -ForegroundColor $colorPrimary
            }
            else {
                Write-Host ' | ' -ForegroundColor $colorIdle -NoNewline
                Write-Host $options[$i].Label -ForegroundColor $options[$i].Color
            }
        }
    }

    while ($true) {
        Show-EnvCopyMenu

        $key = $null
        try {
            $key = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
        }
        catch {
            # Fallback for non-interactive hosts
            $fallback = Read-Host 'Choix [1=.env / 2=.env.template, defaut=2]'
            if ([string]::IsNullOrWhiteSpace($fallback) -or $fallback.Trim() -eq '2') {
                return $false
            }
            if ($fallback.Trim() -eq '1') {
                return $true
            }
            Write-Host 'Entree invalide. Utilise 1 ou 2.' -ForegroundColor $colorWarning
            Start-Sleep -Milliseconds 500
            continue
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 27) {
            throw 'Annule par utilisateur.'
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 38) {
            if ($selection -gt 0) {
                $selection--
            }
            continue
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 40) {
            if ($selection -lt ($options.Count - 1)) {
                $selection++
            }
            continue
        }

        if ($null -ne $key -and $key.VirtualKeyCode -eq 13) {
            return [bool]$options[$selection].Value
        }
    }
}

function Render-Dashboard {
    param(
        [Parameter(Mandatory = $true)][string]$ModeName,
        [Parameter(Mandatory = $true)][string]$CurrentTitle,
        [Parameter(Mandatory = $true)][int]$CurrentStep,
        [Parameter(Mandatory = $true)][int]$TotalSteps,
        [Parameter(Mandatory = $true)][System.Collections.ArrayList]$Steps
    )

    $doneCount = @($Steps | Where-Object { $_.State -eq 'done' }).Count
    $percent = if ($TotalSteps -gt 0) { [int](($doneCount / $TotalSteps) * 100) } else { 0 }
    $barFilled = [char]0x2588
    $barEmpty = [char]0x2591
    $bar = Get-Bar -Percent $percent -Length 30 -FilledChar $barFilled -EmptyChar $barEmpty
    $width = 78
    $h = [char]0x2501
    $hLine = ([string]$h) * ($width - 2)
    $top = ([char]0x250F) + $hLine + ([char]0x2513)
    $mid = ([char]0x2523) + $hLine + ([char]0x252B)
    $bottom = ([char]0x2517) + $hLine + ([char]0x251B)
    $symDone = [char]0x2714
    $symRun = [char]0x2192
    $symFail = [char]0x2716
    $symWait = [char]0x2022

    Clear-Host
    Write-Host $top -ForegroundColor $colorImportant
    Write-BoxLine -Text 'NeedForCrous Setup Wizard - Build en cours' -Width $width -TextColor $colorPrimary -BorderColor $colorImportant
    Write-Host $mid -ForegroundColor $colorImportant
    Write-BoxLine -Text ("Mode      : {0}" -f $ModeName) -Width $width -TextColor $colorSecondary -BorderColor $colorImportant
    Write-BoxLine -Text ("Etape     : {0}/{1}" -f $CurrentStep, $TotalSteps) -Width $width -TextColor $colorSecondary -BorderColor $colorImportant
    Write-BoxLine -Text ("En cours  : {0}" -f $CurrentTitle) -Width $width -TextColor $colorPrimary -BorderColor $colorImportant
    Write-BoxLine -Text ("Progress  : [{0}] {1}%" -f $bar, $percent) -Width $width -TextColor $colorPrimary -BorderColor $colorImportant
    Write-BoxLine -Text ("Log       : {0}" -f $script:LogFile) -Width $width -TextColor $colorSecondary -BorderColor $colorImportant
    Write-Host $bottom -ForegroundColor $colorImportant

    Write-Host ''
    Write-Host 'Etat des etapes' -ForegroundColor $colorImportant
    Write-Host (([string]([char]0x2500)) * 16) -ForegroundColor $colorImportant

    for ($i = 0; $i -lt $Steps.Count; $i++) {
        $step = $Steps[$i]
        $state = $step.State
        $index = $i + 1
        switch ($state) {
            'done' {
                Write-Host (" [{0}] {1}  {2}" -f $index, $symDone, $step.Name) -ForegroundColor $colorSuccess
            }
            'running' {
                Write-Host (" [{0}] {1}  {2}" -f $index, $symRun, $step.Name) -ForegroundColor $colorWarning
            }
            'failed' {
                Write-Host (" [{0}] {1}  {2}" -f $index, $symFail, $step.Name) -ForegroundColor $colorImportant
            }
            default {
                Write-Host (" [{0}] {1}  {2}" -f $index, $symWait, $step.Name) -ForegroundColor $colorSecondary
            }
        }
    }
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Step
    )

    $nativeErrVar = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    $nativeErrPrev = $null
    if ($nativeErrVar) {
        $nativeErrPrev = $nativeErrVar.Value
        $PSNativeCommandUseErrorActionPreference = $false
    }

    $errorActionPrev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        if ($script:BuildMode -eq 'Verbose') {
            Write-Host ("CMD > {0} {1}" -f $File, ($Arguments -join ' ')) -ForegroundColor $colorSecondary
            & $File @Arguments
        }
        else {
            & $File @Arguments *>> $script:LogFile
        }
    }
    finally {
        $ErrorActionPreference = $errorActionPrev
        if ($nativeErrVar) {
            $PSNativeCommandUseErrorActionPreference = $nativeErrPrev
        }
    }

    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    if ($exitCode -ne 0) {
        throw "La commande externe a echoue (code $exitCode) pendant: $Step"
    }
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][int]$Index,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    $script:CurrentStep = $Index + 1
    $step = $script:Steps[$Index]
    $step.State = 'running'

    if ($script:BuildMode -eq 'Quiet') {
        Render-Dashboard -ModeName $script:BuildMode -CurrentTitle $step.Name -CurrentStep $script:CurrentStep -TotalSteps $script:TotalSteps -Steps $script:Steps
    }
    else {
        Write-Host ''
        Write-Host ("[{0}/{1}] {2}" -f $script:CurrentStep, $script:TotalSteps, $step.Name) -ForegroundColor $colorPrimary
    }

    try {
        & $Action
        $step.State = 'done'
        if ($script:BuildMode -eq 'Verbose') {
            Write-Host ("[OK] {0}" -f $step.Name) -ForegroundColor $colorSuccess
        }
    }
    catch {
        $step.State = 'failed'
        if ($script:BuildMode -eq 'Quiet') {
            Render-Dashboard -ModeName $script:BuildMode -CurrentTitle $step.Name -CurrentStep $script:CurrentStep -TotalSteps $script:TotalSteps -Steps $script:Steps
        }
        Write-Host ''
        Write-Host ("[ERREUR] Etape {0}/{1}: {2}" -f $script:CurrentStep, $script:TotalSteps, $step.Name) -ForegroundColor $colorImportant
        Write-Host $_.Exception.Message -ForegroundColor $colorImportant
        if ($script:BuildMode -eq 'Quiet') {
            Write-Host ("Consulte le log: {0}" -f $script:LogFile) -ForegroundColor $colorWarning
        }
        if (-not $NoPause) {
            [void](Read-Host 'Appuie sur Entree pour fermer')
        }
        exit 1
    }
}

$helpAsked = $false
$includeEnvExplicit = $false
foreach ($arg in $LegacyArgs) {
    switch ($arg.ToLowerInvariant()) {
        '-v' { $Mode = 'Verbose' }
        '--verbose' { $Mode = 'Verbose' }
        '-q' { $Mode = 'Quiet' }
        '--quiet' { $Mode = 'Quiet' }
        '--no-pause' { $NoPause = $true }
        '--include-env' {
            $IncludeEnv = $true
            $includeEnvExplicit = $true
        }
        '-h' { $helpAsked = $true }
        '--help' { $helpAsked = $true }
        '/?' { $helpAsked = $true }
        default { }
    }
}

if ($helpAsked) {
    Show-Help
    exit 0
}

try {
    $BuildMode = if ($Mode -eq 'Interactive') { Show-ModeMenu } else { $Mode }

    if ($Mode -eq 'Interactive' -and -not $includeEnvExplicit) {
        $IncludeEnv = Ask-IncludeEnvChoice
    }
}
catch {
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor $colorImportant
    exit 1
}

if (Test-Path $LogFile) {
    Remove-Item $LogFile -Force -ErrorAction SilentlyContinue
}

$pythonExe = if (Test-Path '.venv\\Scripts\\python.exe') {
    Join-Path $ScriptRoot '.venv\\Scripts\\python.exe'
}
else {
    'python'
}

$iconArg = if (Test-Path 'logo.ico') { 'logo.ico' } else { 'NONE' }
$iconMsg = if ($iconArg -eq 'NONE') { 'icone par defaut' } else { 'logo.ico detecte' }

$script:Steps = [System.Collections.ArrayList]::new()
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Verification de Python'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Verification / creation du fichier .env'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Installation / verification de PyInstaller'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Suppression de NeedForCrous.spec'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Nettoyage du dossier build'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Nettoyage du dossier dist'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Compilation de NeedForCrous.exe'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Smoke test de l executable'; State = 'pending' })
[void]$script:Steps.Add([pscustomobject]@{ Name = 'Copie du fichier de configuration dans dist'; State = 'pending' })

$script:CurrentStep = 0
$script:TotalSteps = $script:Steps.Count

if ($BuildMode -eq 'Quiet') {
    Render-Dashboard -ModeName $BuildMode -CurrentTitle 'Initialisation du build' -CurrentStep 0 -TotalSteps $script:TotalSteps -Steps $script:Steps
    Start-Sleep -Milliseconds 400
}
else {
    Clear-Host
    Write-Panel -Title 'NeedForCrous Setup Wizard - Build Started' -Lines @(
        (" Mode   : {0}" -f $BuildMode),
        (" Python : {0}" -f $pythonExe),
        (" Icone  : {0}" -f $iconMsg),
        (" Log    : {0}" -f $LogFile)
    )
}

Invoke-Step -Index 0 -Action {
    Invoke-External -File $pythonExe -Arguments @('--version') -Step 'Verification de Python'
}

Invoke-Step -Index 1 -Action {
    Ensure-EnvConfiguration
}

Invoke-Step -Index 2 -Action {
    $pyInstallerPresent = $true
    try {
        Invoke-External -File $pythonExe -Arguments @('-c', 'import PyInstaller') -Step 'Verification du module PyInstaller'
        if ($BuildMode -eq 'Verbose') {
            Write-Host 'PyInstaller deja disponible dans l environnement Python.' -ForegroundColor $colorSecondary
        }
    }
    catch {
        $pyInstallerPresent = $false
        if ($BuildMode -eq 'Verbose') {
            Write-Host 'PyInstaller absent, lancement de l installation via pip...' -ForegroundColor $colorWarning
        }
    }

    if (-not $pyInstallerPresent) {
        Invoke-External -File $pythonExe -Arguments @('-m', 'pip', 'install', 'pyinstaller') -Step 'Installation de PyInstaller'
    }
}

Invoke-Step -Index 3 -Action {
    if (Test-Path 'NeedForCrous.spec') {
        Remove-Item 'NeedForCrous.spec' -Force
    }
}

Invoke-Step -Index 4 -Action {
    if (Test-Path 'build') {
        Remove-Item 'build' -Recurse -Force
    }
}

Invoke-Step -Index 5 -Action {
    if (Test-Path 'dist') {
        Remove-Item 'dist' -Recurse -Force
    }
}

Invoke-Step -Index 6 -Action {
    $pyArgs = @(
        '-m',
        'PyInstaller',
        '--noconfirm',
        '--onefile',
        '--clean',
        '--name',
        $BuildName,
        '--icon',
        $iconArg,
        '--add-data',
        'src;src',
        '--collect-all',
        'chromedriver_py',
        '--hidden-import=telepot.loop',
        '--hidden-import=telepot.aio.loop',
        '--hidden-import=telepot.delegate',
        'main.py'
    )
    Invoke-External -File $pythonExe -Arguments $pyArgs -Step 'Compilation de NeedForCrous.exe'
}

Invoke-Step -Index 7 -Action {
    if (-not (Test-Path $ExePath)) {
        throw 'Executable introuvable apres compilation, smoke test impossible.'
    }

    Invoke-External -File $ExePath -Arguments @('--help') -Step 'Smoke test de l executable'
}

Invoke-Step -Index 8 -Action {
    if (-not (Test-Path 'dist')) {
        throw 'Le dossier dist est introuvable apres la compilation.'
    }

    if ($IncludeEnv) {
        if (-not (Test-Path $EnvFilePath)) {
            throw 'Le fichier .env est introuvable et ne peut pas etre copie dans dist.'
        }
        Copy-Item -Path $EnvFilePath -Destination (Join-Path $ScriptRoot 'dist\.env') -Force
    }
    else {
        if (-not (Test-Path $EnvTemplatePath)) {
            throw 'Le fichier .env.template est introuvable et ne peut pas etre copie dans dist.'
        }
        Copy-Item -Path $EnvTemplatePath -Destination (Join-Path $ScriptRoot 'dist\.env.template') -Force
    }
}

if ($BuildMode -eq 'Quiet') {
    Render-Dashboard -ModeName $BuildMode -CurrentTitle 'Termine' -CurrentStep $script:TotalSteps -TotalSteps $script:TotalSteps -Steps $script:Steps
    Write-Host ''
}

Write-Panel -Title 'Build Termine Avec Succes' -Lines @(
    (" Executable : {0}" -f $ExePath),
    (" Log file   : {0}" -f $LogFile),
    $(if ($IncludeEnv) { ' Fichier .env copie dans dist\.env' } else { ' Fichier .env.template copie dans dist\.env.template' }),
    ' Tu peux lancer le bot depuis dist\\NeedForCrous.exe'
)

if (-not $NoPause) {
    [void](Read-Host 'Appuie sur Entree pour fermer')
}

exit 0

