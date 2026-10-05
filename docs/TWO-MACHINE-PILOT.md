# Two-machine pilot

There are two stages. Stage A verifies the CBIN exchange and click-only buyer decisions with an explicitly simulated ledger. Stage B uses separate real Odoo 18 test databases, or a configured Zoho Books test organization, and verifies the draft in that accounting system. Stage B has not been executed against customer/vendor accounts. Do not present Stage A as proof of native accounting integration.

## Stage A: one shared host, two independent business sessions

Use a dedicated, empty test deployment with Docker Compose, Python 3.12 and the CBIN package installed. The seller and buyer can use two laptops on the same private network; one laptop or a third host runs the API, worker and PostgreSQL. Both browsers use the same HTTPS address. Each business has its own credential and never shares browser storage. The operator uses a separate session and credential.

1. On the host, choose its private LAN IPv4 address or DNS name, for example `192.168.1.20`. Prepare a private configuration without printing secrets:

   ```sh
   python scripts/prepare_pilot.py --host 192.168.1.20 --bind 192.168.1.20
   docker compose --env-file .env.pilot -f compose.yaml -f compose.pilot.yaml up --build -d
   ```

   The script refuses to overwrite existing configuration. Keep `.env.pilot` private; it is ignored by git. This configuration installs only a reviewed **CBIN canonical JSON demo** profile under the Excel catalogue entry. It is not an Excel workbook/plugin integration and it does not fabricate vendor mappings for other products.

2. Provision test identities once on an empty database and save the credentials privately:

   ```sh
   mkdir -p .pilot
   chmod 700 .pilot
   docker compose --env-file .env.pilot -f compose.yaml -f compose.pilot.yaml run --rm api cbin demo > .pilot/credentials.json
   chmod 600 .pilot/credentials.json
   ```

   This creates seller `submitter`, buyer `reviewer` and CBIN `operator` credentials. Existing databases must use operator onboarding and key issuance instead of `demo`. Do not delete existing data to make this command succeed.

3. Export Caddy's pilot root certificate:

   ```sh
   docker compose --env-file .env.pilot -f compose.yaml -f compose.pilot.yaml cp proxy:/data/caddy/pki/authorities/local/root.crt .pilot/root.crt
   ```

   Install this **public root certificate only** in each machine's trusted certificate store, verify its fingerprint with the host operator, and open `https://192.168.1.20:8443`. Do not transfer Caddy's CA private key. For a normal business deployment use an organization-managed TLS certificate instead. The database has no published port and the API's direct port remains localhost-only. Allow port 8443 only from the two pilot machines/private LAN.

4. Machine 1 connects with the seller credential. Select the `excel / canonical-demo` export profile, choose `examples/invoice.json` and click **Submit export**. Invoice information comes from the file; do not retype it. Native ERP capture uses **Send invoice** instead, after Stage B setup.

5. Machine 2 connects with the buyer credential. Click **Refresh**, or allow up to 15 seconds for automatic receipt polling, and open `DEMO-INV-001`. Click the Supplier field and select **Demo supplier**. Click the accounting item and select **Network cable**. Select **Goods inventory** or **Office supplies** for that line and **Demo tax 15%**. No invoice number, description, quantity, price or total is typed.

6. Click **Review proposed entry**. Confirm USD 200.00 allocated to the selected purchase account, USD 30.00 to the sample tax account, and USD 230.00 to accounts payable. The 15% rate is a test fixture, not a tax determination. Change an account and confirm the prior preview disappears. Review again, then click **Approve & queue bill**. After processing, the reference starts `sandbox:`. This is a simulated result and creates no real ledger entry or payment.

7. Seller refreshes and sees **Draft bill created** with the simulated reference. CBIN operator checks document status, job attempts and the audit timeline. Re-upload the same file: it must return the same document, not a duplicate bill. Use a second source invoice with a new reference to confirm approved supplier/item/account/tax choices are remembered; review and approve each invoice. Purchase orders are never automatically reused.

8. Test rejection, unavailable accounting references, worker stop/restart, repeated source delivery, credential revocation and supplier/account mismatches. Missing references must show an error, not demo replacements. A lost vendor write acknowledgement must require lookup-only reconciliation. See the automated tests and operations guide for the exact mechanisms.

The two-machine TLS/container deployment is provided as configuration; Docker and a second physical machine are unavailable in the coding workspace. Browser automation uses isolated seller/buyer/operator sessions on a disposable server. It does not prove LAN routing, certificate trust or real vendor posting.

## Stage B: real Odoo 18 test accounting records

Use two distinct test databases/companies, seller and buyer, with a configured chart of accounts, journals and currencies. Install `addons/cbin_odoo` in the seller Odoo 18 deployment. Only deployments that permit custom modules can use this capture method. Confirm external API access for the chosen Odoo hosting/plan.

- Set seller company `cbin_business_id=CBIN-DEMO-SELLER` and `cbin_tin=DEMO-TIN-SELLER`. Set its buyer contact to `CBIN-DEMO-BUYER` / `DEMO-TIN-BUYER` using trusted administrator tooling.
- In the buyer database create an active supplier for the seller, active purchasable products with stable codes, eligible purchase/inventory/asset accounts and tax-exclusive percentage purchase tax codes.
- Configure dedicated integration users with access only to their intended company. Keep their passwords/API credentials in the server secret store, never the browser.
- Copy `examples/odoo-connector-config.json`, replace the URLs, database names, users, company and currency IDs with values from each test deployment, then set `CBIN_CONNECTOR_CONFIG` in `.env.pilot`. Set the two password variables and `CBIN_ERP_ALLOWLIST` to the exact ERP base URLs. Both API and worker receive the same configuration. Use HTTPS except for explicitly isolated internal container/network endpoints.
- Restart API and worker after changing configuration. Keep the addon cron disabled while using the seller portal, to make the capture trigger visible during the pilot. Both paths deduplicate source references if they are used together.
- Create and post a seller invoice in Odoo with a two-decimal currency, product codes, positive quantities, no discounts and at most one percentage tax per line. Unsupported shapes are blocked; extend and test the mapping before using them.
- Seller opens its CBIN workspace and clicks **Send invoice** on the real source invoice. Buyer opens the received document, selects its **actual** supplier/product/accounts/taxes by clicking, reviews and approves.
- In the buyer's Odoo database independently inspect the vendor bill: it must remain **draft**, have reference `CBIN-<document-id>`, and exactly match supplier, currency, quantities, unit prices, products, selected accounts, taxes and total. Confirm one bill after repeated source sends. Verify timeout recovery identifies that same bill and blocks mismatched lines.

The Odoo adapter is enabled only in CBIN's test environment. It supports B2B invoices, not refunds, complex taxes or PO matching. Mock JSON-RPC tests cover its request and recovery contracts; actual Odoo installation and accounting reconciliation remain a required acceptance gate. Zoho Books is an alternative buyer test adapter described in `docs/connectors.md` and `docs/BOOKKEEPING.md`.

## Success record

Record the commit, software versions, test database/company IDs, document ID, buyer decision, selected reference IDs, accounting bill ID, amount reconciliation, duplicate/restart results and screenshots from both machines. Do not include API keys/passwords in the record. Keep native acceptance results separate from the 34-product protocol simulation results.
