from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import ee
import os
import json

app = FastAPI()

# Mengaktifkan CORS agar aplikasi Leaflet lancar diakses
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# AUTENTIKASI GOOGLE EARTH ENGINE
# ==============================================================================
gee_credentials = os.environ.get("GEE_CREDENTIALS")
PROJECT_ID = 'imposing-kayak-470402-v4'

if gee_credentials:
    try:
        cred_dict = json.loads(gee_credentials)
        private_key = cred_dict.get('private_key', '').replace('\\n', '\n')
        credentials = ee.ServiceAccountCredentials(cred_dict['client_email'], key_data=private_key)
        ee.Initialize(credentials=credentials, project=PROJECT_ID)
        print("GEE terhubung sukses via Service Account.")
    except Exception as e:
        print(f"Gagal memuat Service Account, mencoba lokal fallback: {e}")
        ee.Initialize(project=PROJECT_ID)
else:
    try:
        ee.Initialize(project=PROJECT_ID)
    except Exception as e:
        print(f"Kredensial tidak ditemukan: {e}")

# Area Padang dengan radius penyaringan 50 km
aoi = ee.Geometry.Point([100.3624642, -0.9242544]).buffer(50000)

# ==============================================================================
# PROCESSING FUNCTIONS
# ==============================================================================
def mask_s2_clouds(image):
    scl = image.select('SCL')
    cloud_mask = (
        scl.neq(3)   # Bayangan awan
        .And(scl.neq(8))  # Awan probabilitas tinggi
        .And(scl.neq(9))  # Awan probabilitas sedang
        .And(scl.neq(10)) # Sirrus
        .And(scl.neq(11)) # Salju/Es
    )
    return image.updateMask(cloud_mask).divide(10000)

def compute_carbon_stock(img):
    ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
    biomass = ndvi.multiply(250).add(20)
    carbon = biomass.multiply(0.47).max(0).rename("carbon")
    return carbon

def get_carbon_raster(start_date_str: str, end_date_str: str):
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(ee.Date(start_date_str), ee.Date(end_date_str))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70))
        .map(mask_s2_clouds)
    )
    return compute_carbon_stock(collection.median())

# ==============================================================================
# WEB INTERFACE (HTML Dashboard)
# ==============================================================================
@app.get("/map", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def map_dashboard():
    # Menetapkan acuan default ke tahun yang sudah pasti memiliki data stabil (contoh: 2024)
    default_year = "2024"
    default_date = "2024-07-01"
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <title>Carbon Multi-Scale Dashboard</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500&family=IBM+Plex+Mono&display=swap">
        
        <style>
            html, body {{ margin:0; height:100%; background:#F7F8F5; overflow:hidden; }}
            #map {{ height:100vh; width: calc(100% - 280px); margin-left: 280px; }}

            .sidebar {{
                position:absolute; top:0; left:0; bottom:0;
                width:280px; background:#F7F8F5; border-right:1px solid #E0E3DC;
                z-index:9999; padding:20px 18px; box-sizing:border-box;
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
            .readout .value {{ font-family:'IBM Plex Mono', monospace; font-size:12px; margin-top:4px; color:#1B2430; }}

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
            .leaflet-control-layers {{ border-radius: 8px !important; font-size: 12px; font-family: 'Inter', sans-serif; }}
        </style>
    </head>
    <body>

    <div class="sidebar">
        <div style="display:flex; align-items:center; gap:8px;">
            <span class="live-dot"></span>
            <span style="font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; color:#3B6D11;">GEE Connected</span>
        </div>
        
        <div class="title">Carbon Multi-Scale</div>
        <div class="coords">Padang Area [100.36, -0.92]</div>
        
        <div class="divider"></div>
        
        <button class="navbtn" id="btn-7day" onclick="switchMode('7day')">7-Day Window</button>
        <button class="navbtn active" id="btn-calendar" onclick="switchMode('calendar')">30-Day Calendar</button>
        <button class="navbtn" id="btn-monthly" onclick="switchMode('monthly')">Monthly Data</button>
        <button class="navbtn" id="btn-yearly" onclick="switchMode('yearly')">Yearly Data</button>
        
        <div class="divider"></div>

        <!-- Bagian Input Dinamis Sidebar -->
        <div id="calendarGroup" class="input-group" style="display: block;">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Tanggal Akhir:</span>
            <input type="date" id="datePicker" value="{default_date}" onchange="fetchCalendarData()">
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
        </div>

        <div id="yearlyGroup" class="input-group">
            <span style="font-size:11px; color:#6B7688; font-weight:500;">Pilih Tahun Analisis:</span>
            <select id="yearSelect" onchange="fetchYearlyData()">
                <option value="2022">Tahun 2022</option>
                <option value="2023">Tahun 2023</option>
                <option value="2024" selected>Tahun 2024</option>
                <option value="2025">Tahun 2025</option>
            </select>
        </div>

        <!-- Output Tampilan Tanggal -->
        <div class="readout">
            <div class="label" id="modeLabel">MODE: 30-DAY CALENDAR</div>
            <div class="value" id="dateRangeDisplay">-</div>
        </div>

        <div class="legend-container">
            <div style="font-size:11px; font-weight:600; color:#6B7688; text-transform:uppercase; letter-spacing:0.5px;">Estimasi Karbon</div>
            <div class="legend-wrapper">
                <div class="legend-bar"></div>
                <div class="legend-ticks">
                    <span>120 Max</span>
                    <span>60 Mid</span>
                    <span>0 Min</span>
                </div>
            </div>
        </div>
    </div>

    <div id="map"></div>

    <script>
        const baseMaps = {{
            "OpenStreetMap": L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'),
            "Google Satellite": L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{ attribution: '© Google' }}),
            "Google Hybrid": L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}', {{ attribution: '© Google Maps' }}),
            "Esri World Imagery": L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{ attribution: 'Tiles © Esri' }})
        }};

        const map = L.map('map', {{
            center: [-0.9242544, 100.3624642],
            zoom: 11,
            layers: [baseMaps["OpenStreetMap"]]
        }});

        map.createPane('carbonPane');
        map.getPane('carbonPane').style.zIndex = 600;
        
        const carbonLayerGroup = L.layerGroup().addTo(map);
        L.control.layers(baseMaps, null, {{ position: 'topright', collapsed: true }}).addTo(map);

        function updateLayer(tileUrl, labelText) {{
            carbonLayerGroup.clearLayers();
            if(!tileUrl || tileUrl.includes("error") || tileUrl === "") {{
                document.getElementById("dateRangeDisplay").innerText = "Data kosong / awan tebal.";
                return;
            }}
            let layer = L.tileLayer(tileUrl, {{ opacity: 0.75, pane: 'carbonPane' }});
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
            document.getElementById("dateRangeDisplay").innerText = "Memproses...";
            let today = new Date(document.getElementById('datePicker').value);
            let sevenDaysAgo = new Date(today);
            sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
            
            let startStr = sevenDaysAgo.toISOString().split('T')[0];
            let endStr = today.toISOString().split('T')[0];

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d.url, `${{startStr}} s/d ${{endStr}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Gagal memuat.");
        }}

        function fetchCalendarData() {{
            document.getElementById("dateRangeDisplay").innerText = "Memproses...";
            let endDateVal = document.getElementById("datePicker").value;
            let endDate = new Date(endDateVal);
            let startDate = new Date(endDate);
            startDate.setDate(startDate.getDate() - 30);

            let startStr = startDate.toISOString().split('T')[0];
            let endStr = endDate.toISOString().split('T')[0];

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d.url, `${{startStr}} s/d ${{endStr}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Gagal memuat.");
        }}

        function fetchMonthlyData() {{
            document.getElementById("dateRangeDisplay").innerText = "Memproses...";
            let month = document.getElementById("monthSelect").value;
            let year = document.getElementById("yearSelect").value || "{default_year}";
            
            let startStr = `${{year}}-${{month}}-01`;
            let endStr = new Date(year, month, 0).toISOString().split('T')[0];

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d.url, `Koleksi Bulan: ${{month}} / ${{year}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Gagal memuat.");
        }}

        function fetchYearlyData() {{
            document.getElementById("dateRangeDisplay").innerText = "Memproses...";
            let year = document.getElementById("yearSelect").value;
            let startStr = `${{year}}-01-01`;
            let endStr = `${{year}}-12-31`;

            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`)
                .then(r => r.json())
                .then(d => updateLayer(d.url, `Rata-rata Citra Tahun ${{year}}`))
                .catch(() => document.getElementById("dateRangeDisplay").innerText = "Gagal memuat.");
        }}

        // Otomatis memanggil data kalender berbasis 2024 saat pertama dimuat
        fetchCalendarData();
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/raster")
def get_raster_tile(start: str, end: str):
    try:
        carbon_img = get_carbon_raster(start, end)
        vis_params = {
            "min": 0,
            "max": 120,
            "palette": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]
        }
        map_id = carbon_img.getMapId(vis_params)
        return {"url": map_id["tile_fetcher"].url_format}
    except Exception as e:
        return {"error": str(e), "url": ""}