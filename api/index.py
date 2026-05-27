from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import ee
import datetime

app = FastAPI()

# ======================
# CORS
# ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# INIT GEE (FIXED SAFE MODE)
# ======================
def init_gee():
    try:
        ee.Initialize(project='imposing-kayak-470402-v4')
        print("GEE initialized successfully")
    except Exception as e:
        print("GEE init failed:", e)
        print("Server will continue running (SAFE MODE)")

init_gee()

# ======================
# AOI PADANG
# ======================
aoi = ee.Geometry.Point([100.3624642, -0.9242544]).buffer(5000)

# ======================
# CLOUD MASK
# ======================
def mask_s2(image):
    scl = image.select('SCL')

    cloud_mask = (
        scl.neq(3)
        .And(scl.neq(8))
        .And(scl.neq(9))
        .And(scl.neq(10))
        .And(scl.neq(11))
    )

    return image.updateMask(cloud_mask).divide(10000)

# ======================
# BASE CARBON MODEL
# ======================
def compute_carbon(img):
    ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
    biomass = ndvi.multiply(250).add(20)
    carbon = biomass.multiply(0.47).max(0).rename("carbon")
    return carbon

# ======================
# SAFE IMAGE LOADER
# ======================
def safe_image(func):
    try:
        return func()
    except Exception as e:
        print("GEE Error:", e)
        return None

# ======================
# 7-DAY WINDOW
# ======================
def get_carbon_window(start_day, end_day):

    today = ee.Date(datetime.datetime.utcnow())

    start_date = today.advance(-end_day, 'day')
    end_date = today.advance(-start_day, 'day')

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(mask_s2)
    )

    if col.size().getInfo() == 0:
        return None

    return compute_carbon(col.median())

# ======================
# MONTHLY
# ======================
def get_carbon_month(year, month):

    start_date = ee.Date.fromYMD(year, month, 1)
    end_date = start_date.advance(1, 'month')

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(mask_s2)
    )

    if col.size().getInfo() == 0:
        return None

    return compute_carbon(col.median())

# ======================
# YEARLY
# ======================
def get_carbon_year(year):

    start_date = ee.Date(f"{year}-01-01")
    end_date = ee.Date(f"{year}-12-31")

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(mask_s2)
    )

    if col.size().getInfo() == 0:
        return None

    return compute_carbon(col.median())

# ======================
# MAP UI
# ======================
@app.get("/map", response_class=HTMLResponse)
def map_view():

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carbon Dashboard</title>

        <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css"/>
        <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>

        <style>
            html, body { margin:0; height:100%; }
            #map { height:100vh; }

            .panel {
                position:absolute;
                top:10px;
                left:50%;
                transform:translateX(-50%);
                background:white;
                padding:12px;
                border-radius:10px;
                z-index:9999;
                width:360px;
            }
        </style>
    </head>

    <body>
    <div id="map"></div>

    <div class="panel">
        <button onclick="load7day()">7-Day</button>
        <button onclick="loadMonth()">Monthly</button>
        <button onclick="loadYear()">Yearly</button>

        <div id="label">Mode: 7-Day</div>
        <div id="dateLabel">-</div>
    </div>

    <script>

    const map = L.map('map').setView([-0.9242544, 100.3624642], 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

    let layers = [];

    function clearLayers(){
        layers.forEach(l => map.removeLayer(l));
        layers = [];
    }

    function load7day(){

        fetch("/carbon-month")
        .then(r => r.json())
        .then(data => {

            clearLayers();

            data.forEach((item,i) => {

                let layer = L.tileLayer(item.tile_url,{opacity:0.7});
                layers.push(layer);

                if(i===0){
                    layer.addTo(map);
                    document.getElementById("dateLabel").innerText = item.label;
                }
            });
        });
    }

    function loadMonth(){

        fetch(`/carbon-month-single/2024/5`)
        .then(r => r.json())
        .then(data => {

            clearLayers();

            let layer = L.tileLayer(data.tile_url,{opacity:0.7});
            layers.push(layer);
            layer.addTo(map);
        });
    }

    function loadYear(){

        fetch(`/carbon-year/2024`)
        .then(r => r.json())
        .then(data => {

            clearLayers();

            let layer = L.tileLayer(data.tile_url,{opacity:0.7});
            layers.push(layer);
            layer.addTo(map);
        });
    }

    load7day();

    </script>

    </body>
    </html>
    """

    return HTMLResponse(html)

# ======================
# ENDPOINTS
# ======================
@app.get("/carbon-month")
def carbon_month():

    windows = [(0,7),(7,14),(14,21),(21,28)]

    today = datetime.datetime.utcnow().date()

    vis = {
        "min": 0,
        "max": 120,
        "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]
    }

    result = []

    for s,e in windows:

        start_date = today - datetime.timedelta(days=e)
        end_date = today - datetime.timedelta(days=s)

        carbon = get_carbon_window(s,e)

        if carbon is None:
            continue

        map_id = carbon.getMapId(vis)

        result.append({
            "label": f"{start_date} → {end_date}",
            "tile_url": map_id["tile_fetcher"].url_format
        })

    return result


@app.get("/carbon-month-single/{year}/{month}")
def carbon_month_single(year:int, month:int):

    carbon = get_carbon_month(year, month)

    if carbon is None:
        return {"error":"no data"}

    vis = {
        "min": 0,
        "max": 120,
        "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]
    }

    map_id = carbon.getMapId(vis)

    return {"tile_url": map_id["tile_fetcher"].url_format}


@app.get("/carbon-year/{year}")
def carbon_year(year:int):

    carbon = get_carbon_year(year)

    if carbon is None:
        return {"error":"no data"}

    vis = {
        "min": 0,
        "max": 120,
        "palette": ["#440154","#3b528b","#21918c","#5ec962","#fde725"]
    }

    map_id = carbon.getMapId(vis)

    return {"tile_url": map_id["tile_fetcher"].url_format}


@app.get("/")
def home():
    return {
        "status": "Carbon Multi-scale Ready",
        "map": "/map"
    }