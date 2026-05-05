import pytest


@pytest.mark.asyncio
async def test_linked_products_page_is_public(client, seed_products):
    response = await client.get("/linked-products")

    assert response.status_code == 200
    assert "Linked Product Families" in response.text
    assert 'name="per_page"' in response.text
    assert '<option value="10" selected>10</option>' in response.text
    assert '<option value="20" >20</option>' in response.text
    assert '<option value="50" >50</option>' in response.text
    assert "SPREI Aurora" in response.text
    assert "BEDCOVER Aurora" in response.text
    assert "Red / S" in response.text
    assert "10 pcs" in response.text
    assert "No variants" in response.text
    assert "PLAIN-SKU-4" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_uses_public_nav_without_login(client, seed_products):
    response = await client.get("/linked-products")

    assert response.status_code == 200
    assert "Linked Products" in response.text
    assert "Logout" not in response.text
    assert "Change Password" not in response.text
    assert 'href="/discounts"' not in response.text
    assert 'href="/"' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_filters_by_jenis_bahan_and_family_code(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&family_code=vr-je9002")

    assert response.status_code == 200
    assert "VR-JE9002" in response.text
    assert "Sprei Microtex Deluxe" in response.text
    assert "BEDCOVER Aurora" not in response.text
    assert "1 linked families" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_filters_by_family_level_variants(client, seed_products):
    response = await client.get("/linked-products?variants=100x200,160x240&min_stock=1")

    assert response.status_code == 200
    assert "VR-JE9002" in response.text
    assert "100x200" in response.text
    assert "160x240" in response.text
    assert "SPREI Aurora" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_supports_partial_and_mixed_width_variant_queries(client, seed_products):
    partial_response = await client.get("/linked-products?variants=100,160x240&min_stock=1")
    spaced_response = await client.get("/linked-products?variants=160%20x%20240&min_stock=1")

    assert partial_response.status_code == 200
    assert "VR-JE9002" in partial_response.text
    assert "Sprei Microtex Deluxe" in partial_response.text
    assert spaced_response.status_code == 200
    assert "VR-JE9002" in spaced_response.text


@pytest.mark.asyncio
async def test_linked_products_page_excludes_families_when_variant_is_missing(client, seed_products):
    response = await client.get("/linked-products?variants=100x200,200x200")

    assert response.status_code == 200
    assert "No linked product families found." in response.text


@pytest.mark.asyncio
async def test_linked_products_page_uses_default_per_page_of_ten(client, seed_products):
    response = await client.get("/linked-products?page=1")

    assert response.status_code == 200
    assert "3 linked families" in response.text
    assert "Page 1 of 2 (3 linked families)" not in response.text
    assert "BC-1001" in response.text
    assert "MP-8002" in response.text
    assert "VR-JE9002" in response.text
    assert 'href="/linked-products?page=2' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_hides_pager_when_filters_fit_one_page(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&page=1&min_stock=1")

    assert response.status_code == 200
    assert "2 linked families" in response.text
    assert "Page 1 of 2 (2 linked families)" not in response.text
    assert 'href="/linked-products?page=2&amp;jenis_bahan=Microtex&amp;min_stock=1&amp;per_page=10"' not in response.text
    assert "MP-8002" in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_invalid_per_page_defaults_to_ten(client, seed_products):
    response = await client.get("/linked-products?per_page=999")

    assert response.status_code == 200
    assert '<option value="10" selected>10</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_shows_selected_per_page_option(client, seed_products):
    response = await client.get("/linked-products?per_page=20")

    assert response.status_code == 200
    assert '<option value="10" >10</option>' in response.text
    assert '<option value="20" selected>20</option>' in response.text
    assert '<option value="50" >50</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_invalid_page_defaults_to_first_page_without_pager(client, seed_products):
    response = await client.get("/linked-products?page=bad")

    assert response.status_code == 200
    assert "3 linked families" in response.text
    assert 'href="/linked-products?page=2' not in response.text
    assert "BEDCOVER Blossom" in response.text
    assert "SPREI Aurora" in response.text
    assert "Sprei Microtex Deluxe" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_defaults_invalid_page_to_first_page_without_pager(client, seed_products):
    response = await client.get("/linked-products?page=bad")

    assert response.status_code == 200
    assert "3 linked families" in response.text
    assert "Page 1 of 2 (3 linked families)" not in response.text
    assert "BEDCOVER Blossom" in response.text
    assert "SPREI Aurora" in response.text
    assert "Sprei Microtex Deluxe" in response.text
    assert 'href="/linked-products?page=2' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_filtered_count_reflects_total_not_current_page(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&page=1&min_stock=1")

    assert response.status_code == 200
    assert "2 linked families" in response.text
    assert "Page 1 of 2 (2 linked families)" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_shows_validation_error_for_invalid_family_code(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=100&family_code=150x220")

    assert response.status_code == 200
    assert "Family code must look like MP-8002 or VR-JE9002" not in response.text
    assert "Internal Server Error" not in response.text
    assert "value=\"100\"" in response.text
    assert "value=\"150x220\"" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_hides_pager_when_filtered_per_page_covers_all_results(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&page=1&min_stock=1&per_page=20")

    assert response.status_code == 200
    assert "2 linked families" in response.text
    assert "Page 1 of 2 (2 linked families)" not in response.text
    assert 'href="/linked-products?page=2&amp;jenis_bahan=Microtex&amp;min_stock=1&amp;per_page=20"' not in response.text
    assert "MP-8002" in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_preserves_selected_per_page_after_filter_submit(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&per_page=50")

    assert response.status_code == 200
    assert '<option value="50" selected>50</option>' in response.text
    assert "2 linked families" in response.text
    assert "Page 1 of 2 (2 linked families)" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_invalid_per_page_with_filters_defaults_to_ten(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&per_page=3")

    assert response.status_code == 200
    assert '<option value="10" selected>10</option>' in response.text
    assert "2 linked families" in response.text
    assert 'href="/linked-products?page=2&amp;jenis_bahan=Microtex' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_default_ten_suppresses_pager_for_three_total_families(client, seed_products):
    response = await client.get("/linked-products?per_page=10")

    assert response.status_code == 200
    assert "3 linked families" in response.text
    assert 'href="/linked-products?page=2&amp;per_page=10"' not in response.text
    assert "BC-1001" in response.text
    assert "MP-8002" in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_large_per_page_options_cover_all_seeded_families(client, seed_products):
    twenty_response = await client.get("/linked-products?per_page=20")
    fifty_response = await client.get("/linked-products?per_page=50")

    assert twenty_response.status_code == 200
    assert fifty_response.status_code == 200
    assert "BC-1001" in twenty_response.text and "VR-JE9002" in twenty_response.text
    assert "BC-1001" in fifty_response.text and "VR-JE9002" in fifty_response.text
    assert 'href="/linked-products?page=2' not in twenty_response.text
    assert 'href="/linked-products?page=2' not in fifty_response.text


@pytest.mark.asyncio
async def test_linked_products_page_selector_lists_supported_options_only(client, seed_products):
    response = await client.get("/linked-products")

    assert response.status_code == 200
    assert '<option value="10"' in response.text
    assert '<option value="20"' in response.text
    assert '<option value="50"' in response.text
    assert '<option value="2"' not in response.text
    assert '<option value="25"' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_invalid_non_numeric_per_page_defaults_to_ten(client, seed_products):
    response = await client.get("/linked-products?per_page=abc")

    assert response.status_code == 200
    assert '<option value="10" selected>10</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_invalid_zero_per_page_defaults_to_ten(client, seed_products):
    response = await client.get("/linked-products?per_page=0")

    assert response.status_code == 200
    assert '<option value="10" selected>10</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_invalid_negative_per_page_defaults_to_ten(client, seed_products):
    response = await client.get("/linked-products?per_page=-10")

    assert response.status_code == 200
    assert '<option value="10" selected>10</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text
    assert "VR-JE9002" in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_keeps_filter_inputs_visible(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&family_code=vr-je9002&variants=100x200,160x240&min_stock=2&per_page=20")

    assert response.status_code == 200
    assert 'value="Microtex"' in response.text
    assert 'value="vr-je9002"' in response.text
    assert 'value="100x200,160x240"' in response.text
    assert 'value="2"' in response.text
    assert '<option value="20" selected>20</option>' in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_keeps_reset_link_simple(client, seed_products):
    response = await client.get("/linked-products?per_page=50")

    assert response.status_code == 200
    assert 'href="/linked-products"' in response.text
    assert '<option value="50" selected>50</option>' in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_still_avoids_internal_error_on_freeform_family_code(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=100&family_code=150x220&per_page=50")

    assert response.status_code == 200
    assert "Internal Server Error" not in response.text
    assert '<option value="50" selected>50</option>' in response.text
    assert 'value="150x220"' in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_does_not_break_no_results_state(client, seed_products):
    response = await client.get("/linked-products?variants=100x200,200x200&per_page=50")

    assert response.status_code == 200
    assert "No linked product families found." in response.text
    assert '<option value="50" selected>50</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_handles_spaces_in_variant_filter(client, seed_products):
    response = await client.get("/linked-products?variants=160%20x%20240&per_page=20")

    assert response.status_code == 200
    assert "VR-JE9002" in response.text
    assert '<option value="20" selected>20</option>' in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_handles_partial_variant_filter(client, seed_products):
    response = await client.get("/linked-products?variants=100,160x240&min_stock=1&per_page=20")

    assert response.status_code == 200
    assert "VR-JE9002" in response.text
    assert '<option value="20" selected>20</option>' in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_handles_family_filter_without_pager(client, seed_products):
    response = await client.get("/linked-products?family_code=vr-je9002&per_page=50")

    assert response.status_code == 200
    assert "VR-JE9002" in response.text
    assert '<option value="50" selected>50</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_custom_per_page_handles_jenis_bahan_filter_without_pager(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&per_page=50")

    assert response.status_code == 200
    assert "MP-8002" in response.text
    assert "VR-JE9002" in response.text
    assert '<option value="50" selected>50</option>' in response.text
    assert 'href="/linked-products?page=2' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_filtered_count_reflects_total_not_current_page(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&page=1&min_stock=1")

    assert response.status_code == 200
    assert "2 linked families" in response.text
    assert "Page 1 of 2 (2 linked families)" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_shows_validation_error_for_invalid_family_code(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=100&family_code=150x220")

    assert response.status_code == 200
    assert "Family code must look like MP-8002 or VR-JE9002" not in response.text
    assert "Internal Server Error" not in response.text
    assert "value=\"100\"" in response.text
    assert "value=\"150x220\"" in response.text
