def invoice_total(lines, tax_rate):
    net = sum(l["qty"] * l["unit_price"] for l in lines)
    return round(net * (1 + tax_rate), 2)
