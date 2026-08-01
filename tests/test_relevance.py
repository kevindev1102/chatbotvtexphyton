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
