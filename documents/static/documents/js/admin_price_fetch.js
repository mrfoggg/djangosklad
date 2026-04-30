document.addEventListener("change", async (event) => {
	if (event.target && event.target.id.endsWith("-product")) {
		const productSelect = event.target;
		const productId = productSelect.value;
		const supplierSelect = document.getElementById("id_supplier");
		const supplierId = supplierSelect ? supplierSelect.value : null;
		const row = productSelect.closest("tr, .inline-related");

		if (supplierId && productId) {
			const url = new URL("/documents/get-price/", window.location.origin);
			url.searchParams.append("supplier_id", supplierId);
			url.searchParams.append("product_id", productId);

			try {
				const response = await fetch(url);
				const data = await response.json();
				const isDark = document.documentElement.classList.contains("dark");

				const priceInput = row.querySelector('input[id$="-price_0"]');
				const currencySelect = row.querySelector('select[id$="-price_1"]');

				// Всегда ставим то, что пришло (цену или 0)
				if (priceInput) {
					priceInput.value = data.price;

					// Подсветка: зеленая при успехе, желтая при 0, красная при ошибке
					const colorClass = data.status === "success" ? "bg-green-100" : data.status === "error" ? "bg-red-100" : "bg-yellow-100";
					const darkColorClass =
						data.status === "success" ? "dark:bg-green-900/30" : data.status === "error" ? "dark:bg-red-900/30" : "dark:bg-yellow-900/30";

					priceInput.classList.add(colorClass, darkColorClass);
					setTimeout(() => priceInput.classList.remove(colorClass, darkColorClass), 1000);
				}

				// Валюта теперь всегда UAH по твоему условию
				if (currencySelect) {
					currencySelect.value = "UAH";
					currencySelect.dispatchEvent(new Event("change", { bubbles: true }));
				}

				// Выводим алерт, используя тексты с сервера
				Swal.fire({
					icon: data.status, // success, info, error
					title: data.title,
					html: data.message,
					toast: true,
					position: "top-end",
					showConfirmButton: false,
					timer: 4000,
					timerProgressBar: true,
					background: isDark ? "#1f2937" : "#fff",
					color: isDark ? "#fff" : "#000",
				});
			} catch (error) {
				console.error("Fetch error:", error);
			}
		}
	}
});
