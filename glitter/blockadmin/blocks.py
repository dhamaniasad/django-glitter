# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from functools import update_wrapper

from django.conf import settings
from django.contrib import messages
from django.contrib.admin import ModelAdmin
from django.contrib.admin.options import InlineModelAdmin
from django.contrib.admin.sites import AdminSite
from django.contrib.admin.utils import unquote, quote
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.urlresolvers import reverse
from django.db.models.base import ModelBase
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.defaultfilters import slugify
from django.template.response import TemplateResponse
from django.utils import six
from django.utils.encoding import force_text
from django.utils.html import escape
from django.utils.translation import ugettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from glitter.models import ContentBlock
from glitter.page import Glitter
from glitter.utils import JSONEncoderForHTML


class BlockAdminSite(AdminSite):
    change_form_template = 'blockadmin/change_form.html'

    def __init__(self, *args, **kwargs):
        # All blocks can be registered to this admin site
        self.block_list = {}

        super(BlockAdminSite, self).__init__(*args, **kwargs)

    # Use the block admin class by default
    def register(self, model_or_iterable, admin_class=None, **options):
        if not admin_class:
            admin_class = BlockAdmin

        category = options.pop('category', None)
        if category:
            self.register_block(model_or_iterable, category)

        super(BlockAdminSite, self).register(model_or_iterable, admin_class, **options)

    # Blocks from the site or other apps can be registered
    def register_block(self, block_or_iterable, category):
        if category not in self.block_list:
            self.block_list[category] = []

        if issubclass(block_or_iterable.__class__, ModelBase):
            self.block_list[category].append(block_or_iterable)
        else:
            for block in block_or_iterable:
                self.block_list[category].append(block)

    def unregister_block(self, block, category):
        try:
            self.block_list[category].remove(block)
        except ValueError:
            pass

    # Remove all of the default admin URLs, we only want the model views for
    # the block admin.
    def get_urls(self):
        from django.conf.urls import url, include

        if settings.DEBUG:
            self.check_dependencies()

        def wrap(view, cacheable=False):
            def wrapper(*args, **kwargs):
                return self.admin_view(view, cacheable)(*args, **kwargs)
            return update_wrapper(wrapper, view)

        # Admin-site-wide views.
        urlpatterns = [
            url(r'^jsi18n/$', wrap(self.i18n_javascript, cacheable=True), name='jsi18n'),
        ]

        # Add in each model's views.
        for model, model_admin in six.iteritems(self._registry):
            urlpatterns += [
                url(r'^%s/%s/' % (model._meta.app_label, model._meta.model_name),
                    include(model_admin.urls))
            ]
        return urlpatterns

    # We have no logout here, so just raise PermissionDenied if needed
    def admin_view(self, view, cacheable=False):
        def inner(request, *args, **kwargs):
            if not self.has_permission(request):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        if not cacheable:
            inner = never_cache(inner)
        # We add csrf_protect here so this function can be used as a utility
        # function for any view, without having to repeat 'csrf_protect'.
        inner = csrf_protect(inner)
        return update_wrapper(inner, view)

    def get_app_list(self, request):
        return {}


class BlockAdmin(ModelAdmin):
    # Keep block admin change forms in the blockadmin template directory
    @property
    def change_form_template(self):
        opts = self.model._meta
        app_label = opts.app_label

        return (
            "blockadmin/%s/%s/change_form.html" % (app_label, opts.object_name.lower()),
            "blockadmin/%s/change_form.html" % app_label,
            "blockadmin/change_form.html"
        )

    # We only want the change form for the block admin
    def get_urls(self):
        from django.conf.urls import url

        def wrap(view):
            def wrapper(*args, **kwargs):
                return self.admin_site.admin_view(view)(*args, **kwargs)
            return update_wrapper(wrapper, view)

        info = self.model._meta.app_label, self.model._meta.model_name

        urlpatterns = [
            url(r'^(.+)/continue/$', wrap(self.continue_view), name='%s_%s_continue' % info),
            url(r'^(.+)/$', wrap(self.edit_view), name='%s_%s_change' % info),
        ]
        return urlpatterns

    def log_change(self, request, object, message):
        pass

    def response_rerender(self, request, obj, template, extra_context=None):
        request.current_app = self.admin_site.name
        content_block = obj.content_block
        version = content_block.obj_version

        glitter = Glitter(version, request=request)
        columns = glitter.render(edit_mode=True, rerender=True)

        context = {
            'column': slugify(content_block.column),
            'rendered_json': JSONEncoderForHTML().encode({
                'content': columns[content_block.column],
            }),
        }

        if extra_context is not None:
            context.update(extra_context)

        return TemplateResponse(request, template, context)

    # A redirect back to the edit view
    def continue_view(self, request, object_id):

        content_block = get_object_or_404(ContentBlock, id=object_id)
        obj = content_block.content_object

        if obj is None:
            raise Http404(_('%(name)s object with primary key %(key)r does not exist.') % {
                'name': force_text(self.opts.verbose_name),
                'key': escape(object_id)},
            )

        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        opts = self.opts.app_label, self.opts.model_name
        change_url = reverse(
            'admin:%s_%s_change' % opts,
            args=(object_id,),
            current_app=self.admin_site.name)

        # This template will rerender the
        return self.response_rerender(request, obj, 'blockadmin/continue.html', {
            'change_url': change_url,
        })

    def has_change_permission(self, request, obj=None):
        # This shouldn't happen - but given that we need to find out the permissions for the glitter
        # object and not just this content block, just fail early incase something goes wrong.
        if obj is None:
            return False

        # Find the glitter object to see if they've got permission to edit that
        version = obj.content_block.obj_version
        glitter_obj = version.content_object
        opts = glitter_obj._meta.app_label, glitter_obj._meta.model_name
        return request.user.has_perm('%s.edit_%s' % opts)

    def edit_view(self, request, object_id, form_url='', extra_context=None):
        """ This view determines if it's add view or change view for the object. """

        content_type = ContentType.objects.get_for_model(self.model)
        self.content_block = get_object_or_404(
            ContentBlock, id=object_id, content_type=content_type
        )

        if self.content_block.object_id:
            # Obj id needs to be passed as string
            return self.change_view(request, str(self.content_block.object_id))
        else:
            return self.changeform_view(request, None)

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """The 'change' admin view for this model."""

        obj = self.get_object(request, unquote(object_id))

        if obj is None:
            raise Http404(_('%(name)s object with primary key %(key)r does not exist.') % {
                'name': force_text(self.opts.verbose_name),
                'key': escape(object_id),
            })

        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        content_block = obj.content_block
        version = content_block.obj_version

        # Version must not be saved, and must belong to this user
        if version.version_number or version.owner != request.user:
            raise PermissionDenied

        return super(BlockAdmin, self).change_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        """Determine the HttpResponse for the change_view stage."""
        opts = self.opts.app_label, self.opts.model_name

        if "_continue" in request.POST:
            msg = _('The %(name)s block was changed successfully. You may edit it again below.') % {
                'name': force_text(self.opts.verbose_name),
            }
            self.message_user(request, msg, messages.SUCCESS)

            # We redirect to the save and continue page, which updates the
            # parent window in javascript and redirects back to the edit page
            # in javascript.
            return HttpResponseRedirect(reverse(
                'admin:%s_%s_continue' % opts, args=(
                    obj.content_block.id,
                ), current_app=self.admin_site.name
            ))

        # Update column and close popup - don't bother with a message as they won't see it
        return self.response_rerender(request, obj, 'admin/glitter/update_column.html')

    def response_add(self, request, obj, post_url_continue=None):

        opts = obj._meta
        msg_dict = {'name': force_text(opts.verbose_name), 'obj': force_text(obj)}

        if obj.content_block is None:

            # Assign object id to content block.
            self.content_block.object_id = obj.id
            self.content_block.save()

            # Assign content block to block.
            obj.content_block = self.content_block
            obj.save()

        if "_continue" in request.POST:
            msg = _(
                'The %(name)s "%(obj)s" was added successfully. You may edit it again below.'
            ) % msg_dict
            self.message_user(request, msg, messages.SUCCESS)
            post_url_continue = reverse(
                'admin:%s_%s_continue' %
                (opts.app_label, opts.model_name),
                args=(quote(obj.content_block.id),),
                current_app=self.admin_site.name
            )
            return HttpResponseRedirect(post_url_continue)

        return self.response_rerender(request, obj, 'admin/glitter/update_column.html')


class InlineBlockAdmin(InlineModelAdmin):
    # For inline objects we always allow adding/editing/deleting. This is an additional check done
    # after the permissions for the object has been checked - everyone needs to be able to edit the
    # same content on the same page, so just allow all inlines.

    def has_add_permission(self, request):
        return True

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return True


class StackedInline(InlineBlockAdmin):
    template = 'admin/edit_inline/stacked.html'


class TabularInline(InlineBlockAdmin):
    template = 'admin/edit_inline/tabular.html'

site = BlockAdminSite(name='block_admin')


def register(model, admin=None, category=None):
    """ Decorator to registering you Admin class. """
    def _model_admin_wrapper(admin_class):

        site.register(model, admin_class=admin_class)

        if category:
            site.register_block(model, category)

        return admin_class
    return _model_admin_wrapper
