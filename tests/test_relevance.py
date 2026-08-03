from app.models import Product, QueryAnalysis
from app.relevance import rank_catalog_products


def product(name: str, category: str, *, available: bool = True) -> Product:
    return Product(
        nombre=name,
        categoria=category,
        categorias=[f"/{category}/"],
        disponible=available,
    )


def test_ipad_query_excludes_accessories_and_cables() -> None:
    products = [
        product("Funda para iPad Pro 13", "Accesorios"),
        product("Cable Lightning a USB Apple", "Accesorios"),
        product("Apple iPad 11 128GB", "Tablets"),
    ]
    analysis = QueryAnalysis(intencion="CONSULTAR_STOCK", busqueda="ipad", categoria="tablet")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Apple iPad 11 128GB"]


def test_laptop_query_excludes_laptop_accessory() -> None:
    products = [
        product("Cargador universal para laptop", "Accesorios"),
        product("Laptop Lenovo IdeaPad Slim 3", "Laptops"),
    ]
    analysis = QueryAnalysis(intencion="CONSULTAR_STOCK", busqueda="laptop", categoria="laptop")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Laptop Lenovo IdeaPad Slim 3"]


def test_accessory_query_can_return_accessories() -> None:
    products = [product("Cargador USB-C para iPhone", "Accesorios")]
    analysis = QueryAnalysis(intencion="BUSCAR_PRODUCTO", busqueda="cargador iphone")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Cargador USB-C para iPhone"]


def test_compatible_apple_accessory_can_match_iphone_without_name_containing_iphone() -> None:
    compatible = product("Cubo de carga USB-C 30W", "Accesorios")
    compatible.marca = "Apple"
    products = [compatible]
    analysis = QueryAnalysis(intencion="BUSCAR_PRODUCTO", busqueda="cargador iphone")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Cubo de carga USB-C 30W"]


def test_accessory_query_excludes_phone_bundle_that_mentions_charger() -> None:
    products = [
        product("iPhone 16 + cargador 20W", "Celulares"),
        product("Cargador de pared Apple USB-C 20W para iPhone", "Cargadores"),
    ]
    analysis = QueryAnalysis(intencion="BUSCAR_PRODUCTO", busqueda="cargador iphone")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Cargador de pared Apple USB-C 20W para iPhone"]


def test_singular_accessory_query_matches_plural_product_name() -> None:
    products = [product("Audífonos JBL Wave Buds", "Audio")]
    products[0].marca = "JBL"
    analysis = QueryAnalysis(
        intencion="BUSCAR_POR_MARCA", busqueda="audifono jbl", marca="jbl", categoria="audio"
    )

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Audífonos JBL Wave Buds"]


def test_accessory_query_does_not_return_main_device() -> None:
    products = [
        product("iPad Air 11 con case incluido", "Tablets"),
        product("Funda para iPad Air 11", "Accesorios"),
    ]
    analysis = QueryAnalysis(intencion="BUSCAR_PRODUCTO", busqueda="funda ipad", categoria="tablet")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Funda para iPad Air 11"]


def test_cellphone_query_excludes_accessories_from_cellphone_category() -> None:
    iphone = Product(
        nombre="iPhone 15 128GB",
        marca="Apple",
        categorias=["/Celulares y Accesorios/Celulares/Apple/"],
        disponible=True,
    )
    airtag = Product(
        nombre="Localizador Apple AirTag 2da Gen",
        marca="Apple",
        categorias=["/Celulares y Accesorios/Accesorios para Celular/Otros Accesorios/"],
        disponible=True,
    )
    apple_watch = Product(
        nombre="Smartwatch Apple Watch Series SE 3",
        marca="Apple",
        categorias=["/Celulares y Accesorios/Smartwatch/Apple/"],
        disponible=True,
    )
    analysis = QueryAnalysis(
        intencion="BUSCAR_POR_MARCA",
        busqueda="celular apple",
        marca="apple",
        categoria="celular",
    )

    result = rank_catalog_products([airtag, apple_watch, iphone], analysis)

    assert result == [iphone]


def test_console_query_excludes_video_games() -> None:
    products = [
        product("UFC 5 (Latam) - PlayStation 5 (PS5)", "Videojuegos"),
        product("Consola PlayStation 5 Slim", "Consolas"),
    ]
    analysis = QueryAnalysis(intencion="CONSULTAR_STOCK", busqueda="playstation 5", categoria="consola")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Consola PlayStation 5 Slim"]


def test_switch_2_query_excludes_lite_and_oled_and_prefers_expensive_results() -> None:
    products = [
        Product(
            nombre="Consola Nintendo Switch Lite 32GB",
            marca="Nintendo",
            categorias=["/Consolas/Nintendo Switch/"],
            precio=975,
            disponible=True,
        ),
        Product(
            nombre="Nintendo Switch OLED 64GB",
            marca="Nintendo",
            categorias=["/Consolas/Nintendo Switch/"],
            precio=1579,
            disponible=True,
        ),
        Product(
            nombre="Consola Nintendo Switch 2 256GB",
            marca="Nintendo",
            categorias=["/Consolas/Nintendo Switch/"],
            precio=2049,
            disponible=True,
        ),
        Product(
            nombre="Consola Nintendo Switch 2 Bundle Mario Kart",
            marca="Nintendo",
            categorias=["/Consolas/Nintendo Switch/"],
            precio=2349,
            disponible=True,
        ),
    ]
    analysis = QueryAnalysis(
        intencion="BUSCAR_POR_MARCA",
        busqueda="nintendo switch 2",
        marca="nintendo",
        categoria="consola",
    )

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == [
        "Consola Nintendo Switch 2 Bundle Mario Kart",
        "Consola Nintendo Switch 2 256GB",
    ]


def test_console_description_can_mention_games_without_being_excluded() -> None:
    console = product("Consola PlayStation 5 Slim", "Consolas")
    console.descripcion = "Incluye dos juegos y control DualSense"
    analysis = QueryAnalysis(intencion="CONSULTAR_STOCK", busqueda="playstation 5", categoria="consola")

    result = rank_catalog_products([console], analysis)

    assert [item.nombre for item in result] == ["Consola PlayStation 5 Slim"]


def test_console_query_excludes_controllers_and_charging_stations() -> None:
    products = [
        product("Mando inalámbrico PlayStation 5 DualSense", "Accesorios de Consolas"),
        product("Estación de carga PlayStation 5 DualSense", "Accesorios Gamer"),
        product("Consola PlayStation 5 Slim", "Consolas"),
    ]
    analysis = QueryAnalysis(intencion="CONSULTAR_STOCK", busqueda="playstation 5", categoria="consola")

    result = rank_catalog_products(products, analysis)

    assert [item.nombre for item in result] == ["Consola PlayStation 5 Slim"]
