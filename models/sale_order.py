from odoo import api, fields, models, _
from odoo.exceptions import UserError
import logging
_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _get_kitchen_pos_session(self):
        PosSession = self.env['pos.session']
        config_name = self.env['ir.config_parameter'].sudo().get_param(
            'website_pos_bridge.kitchen_config_name', 'Kitchen KDS'
        )
        session = PosSession.search([
            ('state', '=', 'opened'),
            ('config_id.name', '=', config_name),
        ], limit=1)
        return session

    def _prepare_pos_order_vals_from_sale(self, session):
        self.ensure_one()
        lines_vals = []
        for so_line in self.order_line:
            if so_line.display_type:
                continue
            product = so_line.product_id
            if not product:
                continue
            if not product.available_in_pos:
                product.available_in_pos = True

            qty = so_line.product_uom_qty
            price_unit = so_line.price_unit
            discount = so_line.discount or 0.0

            # Compute subtotal
            subtotal = price_unit * qty * (1 - (discount / 100.0))
            subtotal_incl = subtotal

            line_vals = {
                'product_id': product.id,
                'qty': qty,
                'price_unit': price_unit,
                'discount': discount,
                'price_subtotal': subtotal,
                'price_subtotal_incl': subtotal_incl,
            }
            lines_vals.append((0, 0, line_vals))

        amount_total = sum(line[2]['price_unit'] * line[2]['qty'] for line in lines_vals)
        amount_tax = 0.0
        amount_paid = 0.0
        amount_return = 0.0

        vals = {
            'company_id': self.company_id.id,
            'config_id': session.config_id.id,
            'session_id': session.id,
            'pricelist_id': self.pricelist_id.id if self.pricelist_id else False,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'fiscal_position_id': self.fiscal_position_id.id if hasattr(self, 'fiscal_position_id') and self.fiscal_position_id else False,
            'pos_reference': _("Web %s") % (self.name,),
            'lines': lines_vals,
            'state': 'draft',

            'amount_total': amount_total,
            'amount_tax': amount_tax,
            'amount_paid': amount_paid,
            'amount_return': amount_return,
        }
        return vals



    def action_confirm(self):
        res = super().action_confirm()
        for order in self:
            try:
                order.message_post(body=_("DEBUG: action_confirm override is running."))

                session = order._get_kitchen_pos_session()
                if not session:
                    order.message_post(
                        body=_("No OPEN Kitchen POS session found (check 'Kitchen KDS'). POS order was NOT created.")
                    )
                    _logger.warning("KDS Bridge: No open session for order %s", order.name)
                    continue

                vals = order._prepare_pos_order_vals_from_sale(session)
                pos_order = self.env['pos.order'].sudo().create(vals)
                order.message_post(body=_("POS Order <b>%s</b> created for Kitchen.") % pos_order.name)
                _logger.info("KDS Bridge: Created POS order %s for sale %s", pos_order.name, order.name)

            except Exception as e:
                # Fail-soft: don't block the sale. Log the reason in chatter.
                msg = _("KDS bridge failed to create POS order: %s") % str(e)
                order.message_post(body=msg)
                _logger.exception("KDS Bridge error on sale %s: %s", order.name, e)
        return res
