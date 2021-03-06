from django.db import models

from .mixins import FileMixin


class BaseCategory(models.Model):
    title = models.CharField(max_length=100, unique=True)

    class Meta:
        abstract = True
        ordering = ('title',)

    def __str__(self):
        return self.title


class FileCategory(BaseCategory):
    class Meta(BaseCategory.Meta):
        verbose_name_plural = 'file categories'


class File(FileMixin, models.Model):
    category = models.ForeignKey(FileCategory)
    title = models.CharField(max_length=100, db_index=True)
    file = models.FileField(upload_to='assets/file')
    file_size = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)


class ImageCategory(BaseCategory):
    class Meta(BaseCategory.Meta):
        verbose_name_plural = 'image categories'


class Image(FileMixin, models.Model):
    category = models.ForeignKey(ImageCategory, blank=True, null=True)
    title = models.CharField(max_length=100, db_index=True)
    file = models.ImageField(
        'Image', upload_to='assets/image', height_field='image_height', width_field='image_width'
    )
    image_height = models.PositiveIntegerField(editable=False)
    image_width = models.PositiveIntegerField(editable=False)
    file_size = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
