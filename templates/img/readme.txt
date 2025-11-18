the images inside this folder are meant for the django-template engine. They are not served as static images, so they can be dynamically styled by the outer context

another solution could be to apply a filter function

medium.com/@union_io/swapping-fill-color-on-image-tag-svgs-using-css-filters-fa4818bf7ec6
https://www.paigeniedringhaus.com/blog/change-svg-color-with-help-from-css-filter
https://codepen.io/sosuke/pen/Pjoqqp

this does not seem worth it as long as svg inputs are small, so the increase in speed from static files is not worth it
