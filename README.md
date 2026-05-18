<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LSCW - LiteSpeed Cache Warmer for any WordPress Site</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 850px;
            margin: 0 auto;
            padding: 20px;
            background-color: #fff;
        }
        h1, h2, h3 {
            color: #111;
            border-bottom: 1px solid #eaecef;
            padding-bottom: 0.3em;
        }
        h1 { font-size: 2.25em; margin-bottom: 20px; }
        h2 { font-size: 1.75em; margin-top: 24px; margin-bottom: 16px; }
        h3 { font-size: 1.25em; margin-top: 24px; margin-bottom: 16px; border-bottom: none; }
        p { margin-top: 0; margin-bottom: 16px; }
        code {
            font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
            background-color: #f6f8fa;
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-size: 85%;
        }
        pre {
            background-color: #f6f8fa;
            padding: 16px;
            border-radius: 6px;
            overflow: auto;
            font-size: 85%;
            line-height: 1.45;
        }
        pre code {
            background-color: transparent;
            padding: 0;
            font-size: 100%;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin-bottom: 16px;
            margin-top: 10px;
        }
        table, th, td {
            border: 1px solid #dfe2e5;
        }
        th, td {
            padding: 8px 12px;
            text-align: left;
        }
        th {
            background-color: #f6f8fa;
        }
        ul, ol {
            padding-left: 2em;
            margin-top: 0;
            margin-bottom: 16px;
        }
        li { margin-bottom: 4px; }
        hr {
            height: 0.25em;
            padding: 0;
            margin: 24px 0;
            background-color: #e1e4e8;
            border: 0;
        }
        blockquote {
            margin: 0 0 16px 0;
            padding: 0 1em;
            color: #6a737d;
            border-left: 0.25em solid #dfe2e5;
        }
    </style>
</head>
<body>

    <h1>LiteSpeed Cache Warmer (LSCW)</h1>
    <p>LSCW is a highly configurable CLI tool designed to pre-warm server-side caches on WordPress sites by simulating the complex "Guest Mode" and "Vary" architecture of LiteSpeed Cache (LSCache).</p>
    <p>Unlike standard web crawlers that only fire superficial GET requests, LSCW processes and handles backend cookie exchanges and browser/device separations natively. This ensures both desktop and mobile cache splits are fully generated and stored on the server before actual users visit the page.</p>

    <hr>

    <h2>Cache Warming Algorithm (3-Phase Mechanism)</h2>
    <p>LiteSpeed Cache relies on a strict cryptographic handshake to serve accurate cached variations. LSCW perfectly mimics this process for every single URL discovered:</p>
    <ol>
        <li>
            <strong>Phase 1 (Initial Guest Visit & Discovery):</strong> 
            Fires an initial <code>GET</code> request to the target URL utilizing standard desktop headers (<code>DESKTOP_HEADERS</code>). The script analyzes the <code>X-LiteSpeed-Cache</code> response header to map out the current state (<code>HIT</code>, <code>MISS</code>, or <code>NO-CACHE</code>) while simultaneously parsing the page source via regex to extract any inline vary hashes.
        </li>
        <li>
            <strong>Phase 2 (Vary Handshake / AJAX Simulation):</strong> 
            Dispatches a precise, structured <code>POST</code> payload (<code>LSCWP_CTRL=before_cloud_init</code>, <code>action=vary_update</code>) directly to the plugin's internal endpoint at <code>/wp-content/plugins/litespeed-cache/guest.vary.php</code>. This forces the backend server to generate and return a unique <code>_lscache_vary</code> cookie value mapped specifically to that asset.
        </li>
        <li>
            <strong>Phase 3 (Full Cache and Variation Generation):</strong>
            <ul>
                <li>The newly acquired vary cookie is appended to the session headers, and a secondary <code>GET</code> request is sent to the URL, establishing a complete <strong>Masaüstü (Desktop) Full Cache</strong> file on disk.</li>
                <li>If explicitly enabled (<code>--mobile</code>), the cookie string is manipulated to inject a <code>device:mobile</code> flag alongside <code>MOBILE_HEADERS</code> to trigger a third sequential request. This guarantees that the independent mobile cache variation is built concurrently.</li>
            </ul>
        </li>
    </ol>

    <hr>

    <h2>Architectural & Engineering Highlights</h2>

    <h3>1. Adaptive Delay Rate Limiting</h3>
    <p>To protect target origins (especially shared hosting or budget VPS infrastructure) from thread fatigue and unexpected <code>429 Too Many Requests</code> errors, the engine employs a dynamic rate-limiting feedback loop:</p>
    <ul>
        <li><strong>Degradation State:</strong> If the engine encounters consecutive HTTP errors or a 429 status code across two iterations, the execution delay (<code>--delay</code>) automatically <strong>doubles</strong>, capping out at a maximum of 4x the original base configuration.</li>
        <li><strong>Stabilization State:</strong> Once a consecutive streak of 10 successful requests is maintained, the controller assumes origin health has recovered and progressively compresses the sleep intervals down by **25%**, settling gradually toward the configured baseline.</li>
    </ul>

    <h3>2. State Persistence & Fault Recovery (Checkpointing)</h3>
    <p>State serialization runs asynchronously, mapping out unique state files for each distinct target (<code>.lscache_&lt;domain_name&gt;.checkpoint.json</code>). Progress is structural and gets committed automatically every 10 successful processing iterations. Passing the <code>--resume</code> flag dynamically loads this matrix, auto-skipping already-warmed assets during service recovery phases.</p>

    <h3>3. Intelligent Sitemap Compiling</h3>
    <p>The parser utilizes a multi-tiered architecture that falls back gracefully to high-performance regex search routines if the system lacks compiled <code>lxml</code> libraries. To bypass aggressive Web Application Firewalls (WAFs) or security plugin locks, sitemap discovery operations are disguised under verified <code>Googlebot</code> User-Agent signatures.</p>

    <h3>4. Automated Retry Sub-Loops</h3>
    <p>Transient connection drops or micro-downtimes do not break execution state. Any URLs dropping due to standard connection timeouts are captured inside a memory stack. Upon pool exhaustion, a dedicated secondary retry sequence sweeps these failures under safer conditions—injecting increased timeout ranges and padded, less aggressive delays.</p>

    <hr>

    <h2>Installation</h2>
    <p>Install the required baseline dependencies before initiating runtime scripts:</p>
    <pre><code>pip install requests lxml rich</code></pre>
    <blockquote>
        <strong>Note:</strong> The <code>rich</code> library is strictly utilized for structural CLI tracking, drawing live progress panels and tabular layout maps. If executed inside bare environments lacking this dependency, the application seamlessly adapts down to <code>Plain log mode</code> over standard <code>stdout</code> loops without losing core logic components.
    </blockquote>

    hr

    <h2>CLI Reference & Parameter Overrides</h2>
    <table>
        <thead>
            <tr>
                <th>Argument</th>
                <th>Type</th>
                <th>Default</th>
                <th>Description</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><code>--site</code></td>
                <td><code>str</code></td>
                <td><em>Required</em></td>
                <td>Base origin web URL to warm (e.g., <code>https://example.com</code>).</td>
            </tr>
            <tr>
                <td><code>--sitemap</code></td>
                <td><code>str</code></td>
                <td><code>None</code></td>
                <td>Explicit sitemap URI source. Auto-resolves to <code>&lt;site&gt;/sitemap.xml</code> if empty.</td>
            </tr>
            <tr>
                <td><code>--urls-file</code></td>
                <td><code>str</code></td>
                <td><code>None</code></td>
                <td>Path to a flat local plain-text file containing line-separated absolute target paths.</td>
            </tr>
            <tr>
                <td><code>--workers</code></td>
                <td><code>int</code></td>
                <td><code>1</code></td>
                <td>Allocated thread execution pool size (Concurrency level).</td>
            </tr>
            <tr>
                <td><code>--delay</code></td>
                <td><code>float</code></td>
                <td><code>1.0</code></td>
                <td>Base cool-down sleep sequence interval between major URL iterations (seconds).</td>
            </tr>
            <tr>
                <td><code>--phase-delay</code></td>
                <td><code>float</code></td>
                <td><code>0.3</code></td>
                <td>Micro-delays applied between consecutive sequential validation checks (Phase 1 → 2 → 3).</td>
            </tr>
            <tr>
                <td><code>--mobile</code></td>
                <td><code>flag</code></td>
                <td><code>True</code></td>
                <td>Toggles whether the execution loop maps independent mobile split-cache assets.</td>
            </tr>
            <tr>
                <td><code>--timeout</code></td>
                <td><code>int</code></td>
                <td><code>30</code></td>
                <td>Maximum connection allowance boundary thresholds per individual HTTP packet exchange.</td>
            </tr>
            <tr>
                <td><code>--start-from</code></td>
                <td><code>int</code></td>
                <td><code>1</code></td>
                <td>Slicing index override parameter (e.g., skip array positions and resume tracking at item 150).</td>
            </tr>
            <tr>
                <td><code>--limit</code></td>
                <td><code>int</code></td>
                <td><code>None</code></td>
                <td>Enforces a strict upper execution roof limit defining maximum URLs processed per loop.</td>
            </tr>
            <tr>
                <td><code>--resume</code></td>
                <td><code>flag</code></td>
                <td><code>False</code></td>
                <td>Instructs workers to load corresponding state sheets and skip past cataloged items.</td>
            </tr>
            <tr>
                <td><code>--dry-run</code></td>
                <td><code>flag</code></td>
                <td><code>False</code></td>
                <td>Compiles, reads, and lists collected target URLs to console screens without executing actual requests.</td>
            </tr>
        </tbody>
    </table>

    <hr>

    <h2>Production Scenarios & Sample Implementations</h2>

    <h3>Safe Single-Thread Execution (Shared Hosting/Low-Spec Specs)</h3>
    <p>Prevents noisy-neighbor alerts by locking concurrency limits down to a single worker while providing clean execution breathing windows across both desktop and mobile distributions:</p>
    <pre><code>python3 lscw.py --site https://example.com --delay 1.5 --workers 1</code></pre>

    <h3>Aggressive Performance Operations (Dedicated VDS / Premium Server Nodes)</h3>
    <p>Spawns 4 concurrent workers interacting over multi-tier nested index tree sitemaps using aggressively lowered timing profiles:</p>
    <pre><code>python3 lscw.py --site https://example.com --sitemap https://example.com/sitemap_index.xml --workers 4 --delay 0.2</code></pre>

    <h3>Resuming Interrupted Large Batches</h3>
    <p>Recovering state context data smoothly across deep catalogs if connections drops occur mid-run:</p>
    <pre><code>python3 lscw.py --site https://example.com --resume --workers 2</code></pre>

    <h3>Pre-Flight URL Evaluation (Dry Run)</h3>
    <p>Validates extraction and target parsing logic without touching remote infrastructure endpoints:</p>
    <pre><code>python3 lscw.py --site https://example.com --dry-run</code></pre>

    <h3>Targeting Isolated Custom Datasets</h3>
    <p>Restricting worker pipelines down to custom arrays read off localized system asset maps:</p>
    <pre><code>python3 lscw.py --site https://example.com --urls-file high_traffic_urls.txt --workers 2</code></pre>

    <hr>

    <h2>Output Metric Clarifications</h2>
    <p>Technical definitions of the diagnostic metadata exposed during execution cycles:</p>
    <ul>
        <li><strong>Guest cache HIT / MISS:</strong> Cache profiling metrics capturing behavior for unauthenticated, freshly initializing client visits stripped of custom header histories.</li>
        <li><strong>Full cache HIT / MISS:</strong> Profile definitions indicating storage behaviors for clients presenting valid, confirmed LiteSpeed vary structures.</li>
        <li><strong>vary.php success:</strong> Represents operational success metrics of the backend interaction loop mapping endpoints at <code>/wp-content/.../guest.vary.php</code>. If this indicator continuously returns flat <code>0</code> values, verify that Guest Mode and optimization layers are explicitly enabled inside the parent WordPress installation.</li>
    </ul>

</body>
</html>
