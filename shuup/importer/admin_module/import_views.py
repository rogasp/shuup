# -*- coding: utf-8 -*-
# This file is part of Shuup.
#
# Copyright (c) 2012-2018, Shuup Inc. All rights reserved.
#
# This source code is licensed under the OSL-3.0 license found in the
# LICENSE file in the root directory of this source tree.
import hashlib
import os
from datetime import datetime

from django.contrib import messages
from django.core.urlresolvers import reverse
from django.http.response import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils.translation import ugettext_lazy as _
from django.views.generic import FormView, TemplateView, View

from shuup.admin.shop_provider import get_shop
from shuup.core.models import Shop, Supplier
from shuup.importer.admin_module.forms import ImportForm, ImportSettingsForm
from shuup.importer.transforms import transform_file
from shuup.importer.utils import (
    get_import_file_path, get_importer, get_importer_choices
)
from shuup.utils.excs import Problem


class ImportProcessView(TemplateView):
    template_name = "shuup/importer/admin/import_process.jinja"
    importer = None

    def dispatch(self, request, *args, **kwargs):
        self.importer_cls = get_importer(request.GET.get("importer"))
        self.model_str = request.GET.get("importer")
        self.shop = Shop.objects.get(pk=request.GET.get("shop"), staff_members=request.user)
        self.lang = request.GET.get("lang")
        return super(ImportProcessView, self).dispatch(request, *args, **kwargs)

    def _transform_request_file(self):
        try:
            filename = get_import_file_path(self.request.GET.get("n"))
            if not os.path.isfile(filename):
                raise ValueError(_("%s is not a file") % self.request.GET.get("n"))
        except:
            raise Problem(_("File missing."))
        try:
            mode = "xls"
            if filename.endswith("xlsx"):
                mode = "xlsx"
            if filename.endswith("csv"):
                mode = "csv"
            return transform_file(mode, filename)
        except (Exception, RuntimeError) as e:
            messages.error(self.request, e)

    def prepare(self):
        self.data = self._transform_request_file()
        if self.data is None:
            return False

        self.importer = self.importer_cls(self.data, self.shop, self.lang)
        self.importer.process_data()

        if self.request.method == "POST":
            # check if mapping was done
            for field in self.importer.unmatched_fields:
                key = "remap[%s]" % field
                vals = self.request.POST.getlist(key)
                if len(vals):
                    self.importer.manually_match(field, vals[0])
            self.importer.do_remap()

        self.settings_form = ImportSettingsForm(data=self.request.POST if self.request.POST else None)
        if self.settings_form.is_bound:
            self.settings_form.is_valid()
        return True

    def post(self, request, *args, **kwargs):
        prepared = self.prepare()
        if not prepared:
            return redirect(reverse("shuup_admin:importer.import"))
        self.importer.do_import(self.settings_form.cleaned_data["import_mode"])
        self.template_name = "shuup/importer/admin/import_process_complete.jinja"
        return self.render_to_response(self.get_context_data(**kwargs))

    def get_context_data(self, **kwargs):
        context = super(ImportProcessView, self).get_context_data(**kwargs)
        context["data"] = self.data
        context["importer"] = self.importer
        context["form"] = self.settings_form
        context["model_fields"] = self.importer.get_fields_for_mapping()
        context["visible_rows"] = self.data.rows[1:5]
        return context

    def get(self, request, *args, **kwargs):
        prepared = self.prepare()
        if not prepared:
            return redirect(reverse("shuup_admin:importer.import"))
        return self.render_to_response(self.get_context_data(**kwargs))


class ImportView(FormView):
    template_name = "shuup/importer/admin/import.jinja"
    form_class = ImportForm

    def post(self, request, *args, **kwargs):
        file = self.request.FILES["file"]
        basename, ext = os.path.splitext(file.name)

        import_name = "%s%s" % (hashlib.sha256(("%s" % datetime.now()).encode("utf-8")).hexdigest(), ext)
        full_path = get_import_file_path(import_name)
        if not os.path.isdir(os.path.dirname(full_path)):
            os.makedirs(os.path.dirname(full_path))

        with open(full_path, 'wb+') as destination:
            for chunk in file.chunks():
                destination.write(chunk)

        next_url = request.POST.get("next")
        importer = request.POST.get("importer")
        shop_id = request.POST.get("shop")
        lang = request.POST.get("language")
        return redirect("%s?n=%s&importer=%s&shop=%s&lang=%s" % (next_url, import_name, importer, shop_id, lang))

    def get_form_kwargs(self):
        kwargs = super(ImportView, self).get_form_kwargs()
        initial = kwargs.get("initial", {})
        initial["importer"] = self.request.GET.get("importer", initial.get("initial"))
        kwargs.update({
            "request": self.request,
            "initial": initial
        })
        return kwargs

    def get_context_data(self, **kwargs):
        context = super(ImportView, self).get_context_data(**kwargs)
        shop = get_shop(self.request)

        context["supplier"] = Supplier.objects.filter(shops=shop).first()

        # check whether the importer has a example file template
        # if so, we also add a url to download the example file
        importer = self.request.GET.get("importer")

        # no importer passed, get the first choice available
        if not importer:
            importers = list(get_importer_choices())
            if importers:
                importer = importers[0][0]

        if importer:
            context["importer"] = get_importer(importer)

        return context


class ExampleFileDownloadView(View):
    def get(self, request, *args, **kwargs):
        importer = request.GET.get("importer")
        file_name = request.GET.get("file_name")
        if not importer or not file_name:
            return HttpResponseBadRequest(_("Invalid parameters"))

        importer_cls = get_importer(importer)
        if not importer_cls or not importer_cls.has_example_file():
            raise Http404(_("Invalid importer"))

        example_file = importer_cls.get_example_file(file_name)
        if not example_file:
            raise Http404(_("Invalid file name"))

        response = HttpResponse(content_type=example_file.content_type)
        response['Content-Disposition'] = 'attachment; filename=%s' % example_file.file_name

        data = importer_cls.get_example_file_content(example_file, request)
        data.seek(0)
        response.write(data.getvalue())
        return response
