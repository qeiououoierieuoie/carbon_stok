from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import ee
import os
import json
from datetime import datetime

app = FastAPI()

# ==============================================================================
# 1. KEAMANAN & AKSESBILITY (CORS)
# ==============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# 2. INITIALISASI GOOGLE EARTH ENGINE (DENGAN FIX UNTUK SERVERLESS VERCEL)
# ==============================================================================
GEE_CREDENTIALS = os.environ.get("GEE_CREDENTIALS")
PROJECT_ID = 'imposing-kayak-470402-v4'  # ID Project Google Cloud Anda

try:
    if GEE_CREDENTIALS:
        cred_dict = json.loads(GEE_CREDENTIALS)
        
        # FIX 1: Perbaiki format newline (\n) langsung di dalam dictionary
        if 'private_key' in cred_dict:
            cred_dict['private_key'] = cred_dict['private_key'].replace('\\n', '\n')
        
        # FIX 2: Masukkan seluruh dictionary yang sudah di-serialize kembali ke json.dumps()
        credentials = ee.ServiceAccountCredentials(
            cred_dict['client_email'], 
            key_data=json.dumps(cred_dict)
        )
        
        ee.Initialize(credentials=credentials, project=PROJECT_ID)
        print("Backend GEE Terkoneksi Sukses.")
    else:
        # Fallback lokal jika dijalankan di PC sendiri sebelum dideploy
        ee.Initialize(project=PROJECT_ID)
        print("Backend GEE Terkoneksi Sukses (Lokal).")
except Exception as e:
    print(f"CRITICAL: Gagal menginisialisasi GEE. Error: {e}")

# Fokus Area: Padang, Indonesia dengan radius aman 20km agar proses komputasi serverless instan
aoi = ee.Geometry.Point([100.3624642, -0.9242544]).buffer(20000)

# ==============================================================================
# 3. ENGINE PROSES DATA SATELIT (OPTIMIZED FOR SPEED)
# ==============================================================================
def mask_s2_clouds(image):
    """Menghapus piksel awan dan bayangan awan menggunakan band SCL Sentinel-2"""
    scl = image.select('SCL')
    cloud_mask = (
        scl.neq(3)         # Bayangan awan
        .And(scl.neq(8))   # Awan probabilitas tinggi
        .And(scl.neq(9))   # Awan probabilitas sedang
        .And(scl.neq(10))  # Sirrus
        .And(scl.neq(11))  # Salju/Es
    )
    return image.updateMask(cloud_mask).divide(10000)

def compute_carbon_stock(img):
    """Menghitung indeks vegetasi (NDVI) menjadi estimasi stok karbon biner"""
    ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
    biomass = ndvi.multiply(250).add(20)
    carbon = biomass.multiply(0.47).max(0).rename("carbon")
    return carbon

def get_carbon_raster(start_date: str, end_date: str):
    """Mengambil koleksi citra Sentinel-2 pada batas area dan rentang tanggal tertentu"""
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(ee.Date(start_date), ee.Date(end_date))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 50)) # Batas toleransi awan diperketat
        .map(mask_s2_clouds)
    )
    
    # Ambil nilai median lalu potong (.clip) ke area Padang agar beban rendering ringan
    composite = collection.median()
    return compute_carbon_stock(composite).clip(aoi)

# ==============================================================================
# 4. ENDPOINT API RASTER (UNTUK DIKONSUMSI FRONTEND LEAFLET)
# ==============================================================================
@app.get("/api/raster")
def get_raster_tile(start: str, end: str):
    try:
        carbon_layer = get_carbon_raster(start, end)
        
        # Palet Warna: Ungu (Karbon Rendah) -> Hijau -> Kuning (Karbon Tinggi)
        vis_params = {
            "min": 0,
            "max": 120,
            "palette": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]
        }
        
        # Minta URL ubin/tile map ID dari server Google
        map_id = carbon_layer.getMapId(vis_params)
        return {"url": map_id["tile_fetcher"].url_format, "status": "success"}
    except Exception as e:
        # Menangkap error internal GEE agar fungsi serverless tidak crash total
        return {"url": "", "status": "error", "message": str(e)}

# ==============================================================================
# 5. FRONTEND INTERFACES (DASBOR UTAMA)
# ==============================================================================
@app.get("/map", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def map_dashboard():
    # Mengunci waktu default ke tanggal hari ini di tahun 2026
    default_date = "2026-07-18"
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Carbon Multi-Scale Dashboard</title>
        
        <!-- Pemanggilan Peta Leaflet.js -->
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        
        <!-- Google Fonts -->
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500&family=IBM+Plex+Mono&display=swap">
        
        <style>
            html, body {{ margin:0; height:100%; background:#F7F8F5; overflow:hidden; }}
            #map {{ height:100vh; width: calc(100% - 280px); margin-left: 280px; z-index: 1; }}

            .sidebar {{
                position:absolute; top:0; left:0; bottom:0;
                width:280px; background:#F7F8F5; border-right:1px solid #E0E3DC;
                z-index:999; padding:20px 18px; box-sizing:border-box;
                font-family:'Inter', sans-serif; color:#1B2430;
                display:flex; flex-direction:column; overflow-y:auto;
            }}
            .live-dot {{ width:7px; height:7px; border-radius:50%; background:#3B6D11; display:inline-block; }}
            .title {{ font-family:'Space Grotesk', sans-serif; font-weight:500; font-size:18px; margin-top:10px; }}
            .coords {{ font-family:'IBM Plex Mono', monospace; font-size:11px; color:#6B7688; margin-top:4px; }}
            .divider {{ height:1px; background:#E0E3DC; margin:16px 0; }}

            .navbtn {{
                all:unset; cursor:pointer; display:flex; align-items:center; gap:10px;
                padding:11px 10px; border-radius:8px; font-size:13px; color:#1B2430;
                border-left:2px solid transparent; margin-bottom:4px; transition: all 0.2s;
            }}
            .navbtn:hover {{ background: rgba(0,0,0,0.04); }}
            .navbtn.active {{ background:#E1F5EE; border-left-color:#0F6E56; font-weight: 500; }}

            .readout {{ background:#FFFFFF; border:1px solid #E0E3DC; border-radius:10px; padding:12px; margin-top:14px;}}
            .readout .label {{ font-size:10px; color:#6B7688; font-weight:600; text-transform: uppercase; letter-spacing:0.3px; }}
            .readout .value {{ font-family:'IBM Plex Mono', monospace; font-size:11px; margin-top:4px; color:#1B2430; word-break: break-word; }}

            select, input[type="date"] {{
                width:100%; background:#FFFFFF; border:1px solid #E0E3DC;
                color:#1B2430; border-radius:8px; padding:8px; box-sizing:border-box;
                font-family:'IBM Plex Mono', monospace; font-size:12px; margin-top:6px;
            }}
            .input-group {{ display:none; margin-top:10px; }}

            .legend-container {{ margin-top: auto; padding-top: 20px; }}
            .legend-wrapper {{ display: flex; align-items: center; gap: 12px; margin-top: 8px; }}
            .legend-bar {{ 
                width: 14px; height: 80px; border-radius: 4px;
                background: linear-gradient(180deg, #fde725, #5ec962, #21918c, #3b528b, #440154); 
                flex-shrink: 0;
            }}
            .legend-ticks {{ 
                display: flex; flex-direction: column; justify-content: space-between;
                height: 80px; font-family: 'IBM Plex Mono', monospace; 
                font-size: 11px; color: #6B7688; line-height: 1;
            }}
        </style>
    </head>
    <body>

    <div class="sidebar">
        <div style="display:flex; align-items:center; gap:8px;">
            <span class="live-dot"></span>
            <span style="font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; color:#3B6D11;">GEE Cloud Connected</span>
        </div>
        
        <div class="title">Carbon Multi-Scale</div>
        <div class="coords">Kota Padang [100.36, -0.92]</div>
        
        <div class="divider"></div>
        
        <button class="navbtn" id="btn-7day" onclick="switchMode('7day')">7-Day Window</button>
        <button class="navbtn active" id="btn-calendar" onclick="switchMode('calendar')">30-Day Calendar</button>
        <button class="navbtn" id="btn-monthly" onclick="switchMode('monthly')">Monthly Data</button>
        <button class="navbtn" id="btn-yearly" onclick="switchMode('yearly')">Yearly Data</button>
        
        <div class="divider"></div>

        <!-- Inputs Konfigurasi Waktu -->
        <div id="calendarGroup" class="input-group" style="display: block;">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Tanggal Akhir:</span>
            <input type="date" id="datePicker" value="{default_date}">
        </div>

        <div id="monthlyGroup" class="input-group">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Bulan:</span>
            <select id="monthSelect" onchange="fetchMonthlyData()">
                <option value="01">Januari</option><option value="02">Februari</option>
                <option value="03">Maret</option><option value="04">April</option>
                <option value="05">Mei</option><option value="06">Juni</option>
                <option value="07" selected>Juli</option><option value="08">Agustus</option>
                <option value="09">September</option><option value="10">Oktober</option>
                <option value="11">November</option><option value="12">Desember</option>
            </select>
            <select id="monthYearSelect" onchange="fetchMonthlyData()" style="margin-top: 4px;">
                <option value="2024">Tahun 2024</option>
                <option value="2025">Tahun 2025</option>
                <option value="2026" selected>Tahun 2026</option>
            </select>
        </div>

        <div id="yearlyGroup" class="input-group">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Tahun Analisis:</span>
            <select id="yearSelect" onchange="fetchYearlyData()">
                <option value="2024">Tahun 2024</option>
                <option value="2025">Tahun 2025</option>
                <option value="2026" selected>Tahun 2026</option>
            </select>
        </div>

        <!-- Box Status Pengambilan Data -->
        <div class="readout">
            <div class="label" id="modeLabel">MODE: 30-DAY CALENDAR</div>
            <div class="value" id="dateRangeDisplay">Memulai mesin peta...</div>
        </div>

        <!-- Legenda Indikator Karbon -->
        <div class="legend-container">
            <div style="font-size:11px; font-weight:600; color:#6B7688; text-transform:uppercase; letter-spacing:0.5px;">Estimasi Stok Karbon</div>
            <div class="legend-wrapper">
                <div class="legend-bar"></div>
                <div class="legend-ticks">
                    <span>120 Max (Ton/Ha)</span>
                    <span>60 Mid</span>
                    <span>0 Min</span>
                </div>
            </div>
        </div>
    </div>

    <div id="map"></div>

    <script>
        // Setup Peta Dasar
        const baseMaps = {{
            "OpenStreetMap": L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'),
            "Google Satellite": L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{ attribution: '© Google' }}),
            "Google Hybrid": L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}', {{ attribution: '© Google Maps' }})
        }};

        const map = L.map('map', {{
            center: [-0.9242544, 100.3624642],
            zoom: 11,
            layers: [baseMaps["OpenStreetMap"]]
        }});

        map.createPane('carbonPane');
        map.getPane('carbonPane').style.zIndex = 600;
        
        const carbonLayerGroup = L.layerGroup().addTo(map);
        L.control.layers(baseMaps, null, {{ position: 'topright' }}).addTo(map);

        function updateLayer(data, labelText) {{
            carbonLayerGroup.clearLayers();
            
            if(!data.url || data.status === "error") {{
                document.getElementById("dateRangeDisplay").innerText = "Gagal memuat: " + (data.message || "Awan tebal/Data kosong");
                return;
            }}
            
            let layer = L.tileLayer(data.url, {{ opacity: 0.75, pane: 'carbonPane' }});
            layer.addTo(carbonLayerGroup);
            document.getElementById("dateRangeDisplay").innerText = labelText;
        }}

        function switchMode(mode) {{
            document.querySelectorAll('.navbtn').forEach(btn => btn.classList.remove('active'));
            document.getElementById("calendarGroup").style.display = "none";
            document.getElementById("monthlyGroup").style.display = "none";
            document.getElementById("yearlyGroup").style.display = "none";

            if (mode === '7day') {{
                document.getElementById('btn-7day').classList.add('active');
                document.getElementById("modeLabel").innerText = "MODE: 7-DAY WINDOW";
                fetch7DayData();
            }} else if (mode === 'calendar') {{
                document.getElementById('btn-calendar').classList.add('active');
                document.getElementById("calendarGroup").style.display = "block";
                document.getElementById("modeLabel").innerText = "MODE: 30-DAY CALENDAR";
                document.getElementById("datePicker").value = "2026-07-18";
                fetchCalendarData();
            }} else if (mode === 'monthly') {{
                document.getElementById('btn-monthly').classList.add('active');
                document.getElementById("monthlyGroup").style.display = "block";
                document.getElementById("modeLabel").innerText = "MODE: MONTHLY COMPOSITE";
                fetchMonthlyData();
            }} else if (mode === 'yearly') {{
                document.getElementById('btn-yearly').classList.add('active');
                document.getElementById("yearlyGroup").style.display = "block";
                document.getElementById("modeLabel").innerText = "MODE: YEARLY COMPOSITE";
                fetchYearlyData();
            }}
        }}

        function fetch7DayData() {{
            document.getElementById("dateRangeDisplay").innerText = "Menghitung data satelit...";
            let endDate = new Date("{default_date}");
            let startDate = new Date(endDate);
            startDate.setDate(endDate.getDate() - 7);
            
            let startStr = startDate.toISOString().split('T')[0];
            let endStr = endDate.toISOString().split('T')[0];

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d, `${{startStr}} s/d ${{endStr}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Koneksi terputus.");
        }}

        function fetchCalendarData() {{
            document.getElementById("dateRangeDisplay").innerText = "Menghitung data satelit...";
            let endDateVal = document.getElementById("datePicker").value;
            let endDate = new Date(endDateVal);
            let startDate = new Date(endDate);
            startDate.setDate(startDate.getDate() - 30);

            let startStr = startDate.toISOString().split('T')[0];
            let endStr = endDate.toISOString().split('T')[0];

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d, `${{startStr}} s/d ${{endStr}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Koneksi terputus.");
        }}

        function fetchMonthlyData() {{
            document.getElementById("dateRangeDisplay").innerText = "Mengolah mosaik bulanan...";
            let month = document.getElementById("monthSelect").value;
            let year = document.getElementById("monthYearSelect").value;
            
            let startStr = `${{year}}-${{month}}-01`;
            let endStr = new Date(year, month, 0).toISOString().split('T')[0];

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d, `Periode: Bulan ${{month}} - ${{year}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Koneksi terputus.");
        }}

        function fetchYearlyData() {{
            document.getElementById("dateRangeDisplay").innerText = "Mengolah komposit tahunan...";
            let year = document.getElementById("yearSelect").value;
            let startStr = `${{year}}-01-01`;
            let endStr = `${{year}}-12-31`;

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d, `Komposit Peta Tahun ${{year}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Koneksi terputus.");
        }}

        // Penambahan event listener stabil pada date picker
        document.getElementById("datePicker").addEventListener("change", fetchCalendarData);

        // Inisialisasi awal saat halaman pertama dibuka
        fetchCalendarData();
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)