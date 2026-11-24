# Publish OneNote sections to PDF through the OneNote desktop COM API (called by export_onenote.py).
# Needs OneNote for Microsoft 365 desktop (ONENOTE.EXE), not the Store app. Read-only on notebooks.
#
#   -JobFile  JSON list of {notebook, section, out, force} (a section), with "page" (a page title) or
#             "page_id" (one page, from -PageList) to publish a single page
#   -List     print every notebook/section instead of publishing
#   -PageList JSON list of {notebook, section}: prints one JSON line per page {notebook, section, id, name,
#             modified} so the caller can export only pages OneNote says changed (export_onenote.py)
param([string]$JobFile, [switch]$List, [string]$PageList)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$on = New-Object -ComObject OneNote.Application
[string]$xml = ""
$on.GetHierarchy("", 3, [ref]$xml)            # 3 = hsSections
[xml]$doc = $xml
$ns = New-Object Xml.XmlNamespaceManager $doc.NameTable
$ns.AddNamespace("one", $doc.DocumentElement.NamespaceURI)

if ($List) {
    foreach ($nb in $doc.SelectNodes("//one:Notebook", $ns)) {
        "$($nb.name)"
        foreach ($s in $nb.SelectNodes("one:Section", $ns)) { "    $($s.name)   (modified $($s.lastModifiedTime))" }
    }
    exit 0
}

if ($PageList) {
    foreach ($q in (Get-Content $PageList -Raw -Encoding UTF8 | ConvertFrom-Json)) {
        $nb = $doc.SelectNodes("//one:Notebook", $ns) | Where-Object { $_.name -eq $q.notebook } | Select-Object -First 1
        $sec = if ($nb) { $nb.SelectNodes("one:Section", $ns) | Where-Object { $_.name -eq $q.section } | Select-Object -First 1 }
        if (-not $sec) { [Console]::Error.WriteLine("  x $($q.notebook) / $($q.section): not found"); continue }
        try { $on.SyncHierarchy($sec.ID) } catch { }
        [string]$px = ""; $on.GetHierarchy($sec.ID, 4, [ref]$px); [xml]$pd = $px
        foreach ($p in $pd.SelectNodes("//one:Page", $ns)) {
            @{ notebook = $q.notebook; section = $q.section; id = $p.ID; name = $p.name.Trim();
               modified = $p.lastModifiedTime } | ConvertTo-Json -Compress
        }
    }
    exit 0
}

$jobs = Get-Content $JobFile -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($j in $jobs) {
    $nb = $doc.SelectNodes("//one:Notebook", $ns) | Where-Object { $_.name -eq $j.notebook } | Select-Object -First 1
    $sec = if ($nb) { $nb.SelectNodes("one:Section", $ns) | Where-Object { $_.name -eq $j.section } | Select-Object -First 1 }
    if (-not $sec) { "  x $($j.notebook) / $($j.section): not found (check names with --list)"; continue }
    if ($j.page_id) {                                      # one page by ID (from -PageList)
        [string]$px = ""; $on.GetHierarchy($sec.ID, 4, [ref]$px); [xml]$pd = $px
        $sec = $pd.SelectNodes("//one:Page", $ns) | Where-Object { $_.ID -eq $j.page_id } | Select-Object -First 1
        if (-not $sec) { "  x $($j.section) / page $($j.page_id): no longer there"; continue }
        $j | Add-Member -NotePropertyName page -NotePropertyValue $sec.name -Force
    }
    elseif ($j.page) {                                     # single page: "Section/Page title"
        [string]$px = ""; $on.GetHierarchy($sec.ID, 4, [ref]$px); [xml]$pd = $px
        $sec = $pd.SelectNodes("//one:Page", $ns) | Where-Object { $_.name.Trim() -eq $j.page.Trim() } | Select-Object -First 1
        if (-not $sec) { "  x $($j.section) / $($j.page): page not found"; continue }
    }
    $label = if ($j.page) { "$($j.section)/$($j.page.Trim())" } else { $j.section }

    $changed = [datetime]::Parse($sec.lastModifiedTime).ToUniversalTime()
    if ((Test-Path $j.out) -and -not $j.force -and (Get-Item $j.out).LastWriteTimeUtc -gt $changed) {
        "  = $($label): unchanged since last export"; continue
    }
    New-Item -ItemType Directory -Force (Split-Path $j.out) | Out-Null
    if (Test-Path $j.out) { Remove-Item $j.out -Force }   # Publish refuses to overwrite
    if (-not $j.page_id) { try { $on.SyncHierarchy($sec.ID) } catch { } }   # pull the latest from SharePoint first
    $on.Publish($sec.ID, $j.out, 3, "")                     # 3 = pfPDF
    $mb = [math]::Round((Get-Item $j.out).Length / 1MB, 1)
    "  + $label -> $(Split-Path $j.out -Leaf) ($mb MB)"
}
