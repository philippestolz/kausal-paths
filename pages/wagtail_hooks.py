from django.forms.widgets import Select
from wagtail.admin.edit_handlers import FieldPanel, ObjectList
from wagtail.contrib.modeladmin.options import ModelAdmin, modeladmin_register
from wagtail.contrib.modeladmin.views import CreateView
from nodes.instance import Instance
from .models import InstanceContent, NodeContent


def get_instance(request) -> Instance:
    from .global_instance import instance
    return instance


class NodeEditHandler(ObjectList):
    def __init__(self, *args, **kwargs):
        panels = [
            FieldPanel('node_id', widget=Select()),
            FieldPanel('short_description'),
            FieldPanel('body'),
        ]
        if 'children' not in kwargs:
            kwargs['children'] = panels
        super().__init__(*args, **kwargs)

    def get_form_class(self):
        instance = get_instance(self.request)
        context = instance.context
        klass = super().get_form_class()
        field = klass.base_fields['node_id']
        choices = [(node_id, node.name) for node_id, node in context.nodes.items()]
        field.widget.choices = choices
        return klass


class NodeCreateView(CreateView):
    def get_instance(self):
        obj = super().get_instance()
        instance = get_instance(self.request)
        iobj = InstanceContent.objects.filter(identifier=instance.id).first()
        if iobj is None:
            iobj = InstanceContent(identifier=instance.id)
            iobj.save()
        obj.instance = iobj
        return obj


class NodeAdmin(ModelAdmin):
    model = NodeContent
    menu_icon = 'fa-line-chart'
    menu_order = 200
    list_display = ('name', 'node_id')
    search_fields = ('name', 'node_id')
    edit_handler = NodeEditHandler()
    create_view_class = NodeCreateView


modeladmin_register(NodeAdmin)
