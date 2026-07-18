from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import ee
import os
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# ALUR INISIALISASI LAZY LOADING GEE
# ==============================================================================
GEE_CREDENTIALS = os.environ.get("GEE_CREDENTIALS")
PROJECT_ID = 'imposing-kayak-470402-v4' 

def initialize_gee_lazy():
    if not getattr(initialize_gee_lazy, "done", False):
        try:
            if GEE_CREDENTIALS:
                cred_dict = json.loads(GEE_CREDENTIALS)
                if 'private_key' in cred_dict:
                    cred_dict['private_key'] = cred_dict['private_key'].replace('\\n', '\n')
                
                credentials = ee.ServiceAccountCredentials(
                    cred_dict['client_email'], 
                    key_data=json.dumps(cred_dict)
                )
                ee.Initialize(credentials=credentials, project=PROJECT_ID)
            else:
                ee.Initialize(project=PROJECT_ID)
            initialize_gee_lazy.done = True
            initialize_gee_lazy.error = None
            print("GEE Terkoneksi Sukses di Cloud.")
        except Exception as e:
            initialize_gee_lazy.error = str(e)
            print(f"CRITICAL ERROR: Gagal inisialisasi GEE: {e}")
    return getattr(initialize_gee_lazy, "error", None)

def get_aoi():
    return ee.Geometry.Point([100.3624642, -0.9242544]).buffer(20000)

def mask_s2_clouds(image):
    scl = image.select('SCL')
    cloud_mask = (scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11)))
    return image.updateMask(cloud_mask).divide(10000)

def compute_carbon_stock(img):
    ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
    biomass = ndvi.multiply(250).add(20)
    return biomass.multiply(0.47).max(0).rename("carbon")

def get_carbon_raster(start_date: str, end_date: str):
    aoi = get_aoi()
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(ee.Date(start_date), ee.Date(end_date))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 50))
        .map(mask_s2_clouds)
    )
    return compute_carbon_stock(collection.median()).clip(aoi)

# ==============================================================================
# API ENDPOINT
# ==============================================================================
@app.get("/api/raster")
def get_raster_tile(start: str, end: str):
    error = initialize_gee_lazy()
    if error:
        return {"url": "", "status": "error", "message": f"GEE Error: {error}"}
    try:
        carbon_layer = get_carbon_raster(start, end)
        vis_params = {
            "min": 0, "max": 120,
            "palette": ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]
        }
        map_id = carbon_layer.getMapId(vis_params)
        return {"url": map_id["tile_fetcher"].url_format, "status": "success"}
    except Exception as e:
        return {"url": "", "status": "error", "message": str(e)}

@app.get("/map", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def map_dashboard():
    default_date = "2026-07-18"
    html_content = f"""
    <!DOCTYPE html>
    <html lang="id">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Carbon Multi-Scale Dashboard</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500&family=IBM+Plex+Mono&display=swap">
        <style>
            html, body {{ margin:0; padding:0; height:100%; width:100%; background:#F7F8F5; font-family:'Inter', sans-serif; overflow:hidden; }}
            
            /* Susunan Grid Desktop Utama */
            .app-container {{ display: flex; flex-direction: row; height: 100vh; width: 100vw; overflow: hidden; position: relative; }}
            
            /* Peta Desktop di Sisi Kanan */
            #map {{ flex-grow: 1; height: 100%; z-index: 1; position: relative; }}
            
            /* Handle drag mobile disembunyikan total di laptop */
            .drag-handle {{ display: none; }}

            /* ==============================================================================
               KEMBALI KE ASLI: STYLE DESKTOP LAPTOP PERSIS SEPERTI DI GAMBAR ANDA
               ============================================================================== */
            .sidebar {{
                width: 280px; background:#F7F8F5; border-right:1px solid #E0E3DC; 
                z-index:999; padding:20px 18px; box-sizing:border-box;
                color:#1B2430; display:flex; flex-direction:column; overflow-y:auto; flex-shrink: 0;
            }}
            
            .live-dot {{ width:7px; height:7px; border-radius:50%; background:#3B6D11; display:inline-block; }}
            .title {{ font-family:'Space Grotesk', sans-serif; font-weight:500; font-size:18px; margin-top:10px; }}
            .coords {{ font-family:'IBM Plex Mono', monospace; font-size:11px; color:#6B7688; margin-top:4px; }}
            .divider {{ height:1px; background:#E0E3DC; margin:16px 0; }}
            
            .btn-group {{ display: flex; flex-direction: column; gap: 4px; }}
            .navbtn {{
                all:unset; cursor:pointer; display:flex; align-items:center; box-sizing: border-box;
                padding:11px 10px; border-radius:8px; font-size:13px; color:#1B2430; width: 100%;
            }}
            .navbtn.active {{ background:#E1F5EE; border-left:2px solid #0F6E56; font-weight: 500; }}
            
            .readout {{ background:#FFFFFF; border:1px solid #E0E3DC; border-radius:10px; padding:12px; margin-top:14px;}}
            .readout .label {{ font-size:10px; color:#6B7688; font-weight:600; text-transform: uppercase; }}
            .readout .value {{ font-family:'IBM Plex Mono', monospace; font-size:11px; margin-top:4px; color:#1B2430; word-break: break-all; }}
            
            select, input[type="date"] {{
                width:100%; background:#FFFFFF; border:1px solid #E0E3DC; border-radius:8px; 
                padding:8px; font-family:'IBM Plex Mono', monospace; font-size:12px; margin-top:6px; box-sizing: border-box;
            }}
            .input-group {{ display:none; margin-top:10px; }}
            
            .legend-container {{ margin-top:auto; padding-top:20px; }}
            .legend-bar {{ width: 14px; height: 80px; border-radius: 4px; background: linear-gradient(180deg, #fde725, #5ec962, #21918c, #3b528b, #440154); }}

            /* ==============================================================================
               KHUSUS MOBILE ONLY: POSISI FLOATING BOTTOM SHEET NAIK LEBIH KEATAS AGAR AMAN
               ============================================================================== */
            @media screen and (max-width: 768px) {{
                /* Peta dipaksa fullscreen di belakang layar HP */
                #map {{
                    position: absolute !important;
                    top: 0 !important; left: 0 !important; right: 0 !important; bottom: 0 !important;
                    width: 100% !important; height: 100% !important;
                    z-index: 1 !important;
                }}
                
                /* POSISI BARU: Sengaja dinaikkan bottom-nya agar muat utuh di layar HP */
                .sidebar {{
                    position: absolute !important;
                    bottom: 30px !important; /* Diubah dari 12px ke 80px agar terangkat ke atas */
                    left: 12px !important;
                    right: 12px !important;
                    top: auto !important;
                    width: calc(100% - 24px) !important;
                    height: auto !important;
                    max-height: 60vh !important; /* Ditambah tinggi maksimalnya agar lebih leluasa */
                    background: rgba(255, 255, 255, 0.96) !important;
                    backdrop-filter: blur(10px);
                    -webkit-backdrop-filter: blur(10px);
                    border: 1px solid rgba(224, 227, 220, 0.8) !important;
                    border-radius: 24px !important;
                    box-shadow: 0px -8px 32px rgba(0, 0, 0, 0.15) !important;
                    padding: 8px 16px 20px 16px !important;
                    z-index: 9999 !important;
                    overflow-y: auto !important;
                }}
                
                /* Memunculkan garis drag handle ala Google Maps */
                .drag-handle {{
                    display: block !important;
                    width: 40px;
                    height: 4px;
                    background: #CBD5E1;
                    border-radius: 2px;
                    margin: 4px auto 14px auto;
                }}
                
                /* Hilangkan elemen sekunder demi space HP */
                .coords, .divider, .legend-container {{ display: none !important; }}
                
                .title {{ font-size: 16px; margin-top: 2px; margin-bottom: 10px; font-weight:600; }}
                
                /* Tombol Menu jadi berjejer horizontal samping-sampingan di HP */
                .btn-group {{ flex-direction: row !important; gap: 6px !important; width: 100% !important; }}
                .navbtn {{ 
                    justify-content: center !important; 
                    padding: 10px 6px !important; 
                    font-size: 12px !important; 
                    text-align: center !important;
                    border: 1px solid #E0E3DC !important;
                    border-radius: 12px !important;
                    background: #FFFFFF !important;
                }}
                .navbtn.active {{ 
                    background: #1B2430 !important; 
                    color: #FFFFFF !important; 
                    border-color: #1B2430 !important; 
                    border-left: none !important; 
                    font-weight: 500 !important; 
                }}
                
                .readout {{ margin-top: 10px !important; padding: 10px !important; border-radius: 12px !important; }}
                select, input[type="date"] {{ padding: 8px !important; font-size: 12px !important; border-radius: 10px !important; }}
                
                /* Pindahkan kontrol zoom bawaan ke kanan atas agar tidak tabrakan */
                .leaflet-top.leaflet-left {{ top: 12px !important; left: auto !important; right: 12px !important; }}
            }}
        </style>
    </head>
    <body>
    <div class="app-container">
        <!-- Sidebar kiri (Desktop) / Bottom Sheet melayang (Mobile) -->
        <div class="sidebar">
            <div class="drag-handle"></div>
            
            <div style="display:flex; align-items:center; gap:8px;">
                <span class="live-dot"></span>
                <span style="font-size:9px; font-weight:600; color:#3B6D11;">GEE CLOUD CONNECTED</span>
            </div>
            <div class="title">Carbon Multi-Scale</div>
            <div class="coords">Kota Padang [100.36, -0.92]</div>
            <div class="divider"></div>
            
            <div class="btn-group">
                <button class="navbtn active" id="btn-calendar" onclick="switchMode('calendar')">30-Day Calendar</button>
                <button class="navbtn" id="btn-yearly" onclick="switchMode('yearly')">Yearly Data</button>
            </div>
            
            <div id="calendarGroup" class="input-group" style="display: block;">
                <input type="date" id="datePicker" value="{default_date}">
            </div>
            <div id="yearlyGroup" class="input-group">
                <select id="yearSelect" onchange="fetchYearlyData()">
                    <option value="2024">Tahun 2024</option>
                    <option value="2025">Tahun 2025</option>
                    <option value="2026" selected>Tahun 2026</option>
                </select>
            </div>
            <div class="readout">
                <div class="label" id="modeLabel">MODE: 30-DAY CALENDAR</div>
                <div class="value" id="dateRangeDisplay">Memproses mesin peta...</div>
            </div>
            
            <div class="legend-container">
                <div style="font-size:11px; font-weight:600; color:#6B7688;">ESTIMASI STOK KARBON (Ton/Ha)</div>
                <div style="display:flex; gap:12px; margin-top:8px;">
                    <div class="legend-bar"></div>
                    <div style="display:flex; flex-direction:column; justify-content:space-between; height:80px; font-family:'IBM Plex Mono'; font-size:11px; color:#6B7688;">
                        <span>120 Max</span><span>60 Mid</span><span>0 Min</span>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Peta Utama -->
        <div id="map"></div>
    </div>
    <script>
        const baseMaps = {{
            "OpenStreetMap": L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'),
            "Google Satellite": L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{ attribution: '© Google' }})
        }};
        const map = L.map('map', {{ center: [-0.9242544, 100.3624642], zoom: 11, layers: [baseMaps["OpenStreetMap"]] }});
        
        // Atur posisi default tombol zoom ke pojok kiri atas (aman untuk desktop)
        // Di mobile, script CSS media query otomatis akan memindahkannya agar tidak tabrakan
        map.zoomControl.setPosition('topleft');
        
        map.createPane('carbonPane').style.zIndex = 600;
        const carbonLayerGroup = L.layerGroup().addTo(map);

        function updateLayer(data, labelText) {{
            carbonLayerGroup.clearLayers();
            if(!data.url || data.status === "error") {{
                document.getElementById("dateRangeDisplay").innerText = "Gagal memuat: " + (data.message || "Terjadi kendala");
                return;
            }}
            L.tileLayer(data.url, {{ opacity: 0.75, pane: 'carbonPane' }}).addTo(carbonLayerGroup);
            document.getElementById("dateRangeDisplay").innerText = labelText;
        }}

        function switchMode(mode) {{
            document.getElementById("btn-calendar").classList.toggle("active", mode === 'calendar');
            document.getElementById("btn-yearly").classList.toggle("active", mode === 'yearly');
            document.getElementById("calendarGroup").style.display = mode === 'calendar' ? 'block' : 'none';
            document.getElementById("yearlyGroup").style.display = mode === 'yearly' ? 'block' : 'none';
            document.getElementById("modeLabel").innerText = mode === 'calendar' ? "MODE: 30-DAY CALENDAR" : "MODE: YEARLY DATA";
            if(mode === 'calendar') fetchCalendarData();
            if(mode === 'yearly') fetchYearlyData();
        }}

        function fetchCalendarData() {{
            document.getElementById("dateRangeDisplay").innerText = "Menghitung...";
            let endDate = new Date(document.getElementById("datePicker").value);
            let startDate = new Date(endDate); startDate.setDate(startDate.getDate() - 30);
            let startStr = startDate.toISOString().split('T')[0];
            let endStr = endDate.toISOString().split('T')[0];
            fetch(`/api/raster?start=${{startStr}}&end=${{endStr}}`).then(r => r.json()).then(d => updateLayer(d, `${{startStr}} s/d ${{endStr}}`));
        }}

        function fetchYearlyData() {{
            document.getElementById("dateRangeDisplay").innerText = "Membuat komposit...";
            let year = document.getElementById("yearSelect").value;
            fetch(`/api/raster?start=${{year}}-01-01&end=${{year}}-12-31`).then(r => r.json()).then(d => updateLayer(d, `Komposit Tahun ${{year}}`));
        }}

        document.getElementById("datePicker").addEventListener("change", fetchCalendarData);
        
        setTimeout(() => {{
            fetchCalendarData();
        }}, 500);
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)